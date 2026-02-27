"""Lambda handler — entry point for scheduled execution.

Every invocation runs two phases:
  Phase 1: Maintenance API → unhealthy services → Datadog drill-down → Kong attribution
  Phase 2: Standard preset analysis → anomalies → Slack/Jira/S3
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime

import httpx

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy httpx/httpcore debug logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_DEFAULT_MAINTENANCE_URL = (
    "https://api.prod.jaja.finance/svc/v3/services/maintenance"
    "?platform=IOS&brand=jaja"
)


def handler(event: dict, context) -> dict:
    """Lambda entry point — triggered by EventBridge every 5 minutes.

    Runs two phases every invocation:
      Phase 1: Call maintenance API, diagnose unhealthy services via Datadog.
      Phase 2: Run standard preset analysis (anomalies, Slack, Jira, S3 reports).

    Event overrides (all optional):
      {"maintenance_url": "https://...", "time_range": "last 1h", "limit": 500}
    """
    logger.info("dd-log-analyzer Lambda invoked")
    logger.info(f"Event: {json.dumps(event)}")

    # ── Load config ──
    from dd_log_analyzer.config_aws import load_config_from_aws

    config = load_config_from_aws()
    logger.info(f"Config loaded — scope: {config.scope.env}, presets: {list(config.presets.keys())}")

    # ── Initialize services ──
    from dd_log_analyzer.analysis.engine import AnalysisEngine
    from dd_log_analyzer.client import DatadogLogClient
    from dd_log_analyzer.notifications.dynamo_alert_state import DynamoAlertState
    from dd_log_analyzer.notifications.jira import JiraNotifier
    from dd_log_analyzer.notifications.slack import SlackNotifier
    from dd_log_analyzer.query.engine import QueryEngine, parse_time_range
    from dd_log_analyzer.reporting.html_report import generate_html_report
    from dd_log_analyzer.reporting.json_report import generate_json_report
    from dd_log_analyzer.reporting.s3_report import S3ReportUploader

    client = DatadogLogClient(config)
    engine = QueryEngine(client, config)
    analysis_engine = AnalysisEngine(config)

    dynamodb_table = os.environ.get("DYNAMODB_TABLE", "dd-log-analyzer-alert-state")
    s3_bucket = os.environ.get("S3_REPORT_BUCKET", "")
    region = os.environ.get("AWS_REGION_NAME", "eu-west-2")

    alert_state = DynamoAlertState(table_name=dynamodb_table, region=region)
    slack_notifier = SlackNotifier(config, alert_state)
    jira_notifier = JiraNotifier(config, alert_state)
    s3_uploader = S3ReportUploader(bucket_name=s3_bucket, region=region) if s3_bucket else None

    # AI-powered anomaly descriptions
    from dd_log_analyzer.analysis.ai_describer import AnomalyDescriber
    ai_describer = AnomalyDescriber(region=region)

    maintenance_url = event.get("maintenance_url", _DEFAULT_MAINTENANCE_URL)
    time_range = "last 10m"

    summary = {
        "timestamp": datetime.utcnow().isoformat(),
        "maintenance": {},
        "presets_analyzed": [],
        "total_anomalies": 0,
        "slack_alerts_sent": 0,
        "jira_tickets_created": 0,
        "reports_uploaded": [],
    }

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║  Phase 1 — Maintenance API health check                      ║
    # ╚═══════════════════════════════════════════════════════════════╝
    logger.info(f"Phase 1: Checking maintenance API — {maintenance_url}")

    try:
        resp = httpx.get(
            maintenance_url,
            headers={"x-jaja-version": "v2.0"},
            timeout=10,
        )
        resp.raise_for_status()
        maint_data = resp.json()
    except Exception as maint_err:
        logger.warning(f"Maintenance API unreachable: {maint_err}")
        maint_data = {"state": "OK", "maintenance": []}

    maint_state = maint_data.get("state", "OK")
    maint_services = maint_data.get("maintenance", [])

    summary["maintenance"]["state"] = maint_state
    summary["maintenance"]["unhealthy_services"] = len(maint_services)
    summary["maintenance"]["diagnoses"] = []

    if maint_services:
        logger.info(f"🚨 {len(maint_services)} unhealthy service(s): {maint_services}")

        time_from, time_to = parse_time_range(time_range)
        limit = event.get("limit", 500)

        for svc in maint_services:
            if isinstance(svc, str):
                svc_name = svc
            elif isinstance(svc, dict):
                svc_name = svc.get("name", svc.get("service", str(svc)))
            else:
                svc_name = str(svc)

            logger.info(f"── Diagnosing: {svc_name} ──")
            dd_query = f"service:{svc_name} status:(error OR critical)"

            try:
                aggregation = None
                try:
                    aggregation = engine.aggregate(
                        raw=dd_query,
                        time_range=time_range,
                        group_by=["service", "status"],
                    )
                except Exception:
                    pass

                logs = engine.query(raw=dd_query, time_range=time_range, limit=limit)

                if not logs and (not aggregation or aggregation.total == 0):
                    # No DD errors → Kong issue
                    logger.info(f"  {svc_name}: No DD errors → Kong issue")

                    diagnosis = {
                        "service": svc_name,
                        "errors": 0,
                        "verdict": "KONG_ISSUE",
                        "detail": "Service healthy in Datadog — Kong cannot route to it",
                        "recommendation": "Check Kong routes, upstreams, and health-check config",
                    }
                    summary["maintenance"]["diagnoses"].append(diagnosis)

                    # Slack alert for Kong issue
                    if config.slack.enabled:
                        try:
                            slack_notifier._send_webhook({
                                "text": (
                                    f"🔀 *KONG ISSUE — {svc_name}*\n"
                                    f"Maintenance API reports unhealthy, "
                                    f"but Datadog has 0 errors.\n"
                                    f"→ Check Kong routes & upstreams."
                                ),
                            })
                            summary["slack_alerts_sent"] += 1
                        except Exception:
                            pass

                    continue

                # Analyse
                result = analysis_engine.analyze(
                    logs=logs, query=dd_query,
                    time_from=time_from, time_to=time_to,
                    aggregation=aggregation,
                )

                diagnosis = {
                    "service": svc_name,
                    "errors": len(logs),
                    "anomalies": len(result.anomalies),
                    "error_groups": len(result.error_groups),
                    "trend": result.trends.trend_direction,
                    "verdict": "SERVICE_ERROR",
                }

                if result.anomalies:
                    diagnosis["anomaly_details"] = [
                        {
                            "type": a.anomaly_type.value,
                            "severity": a.severity.value,
                            "description": a.description,
                        }
                        for a in result.anomalies
                    ]

                if result.error_groups:
                    diagnosis["top_errors"] = [
                        {
                            "count": eg.count,
                            "sample": eg.sample_messages[0][:100] if eg.sample_messages else "",
                            "root_cause": eg.root_cause_candidate,
                        }
                        for eg in result.error_groups[:5]
                    ]

                # Jira + Slack for service anomalies
                jira_keys: dict[str, str] = {}
                if config.jira.enabled and result.anomalies:
                    jira_keys = jira_notifier.create_tickets_from_analysis(result)
                    summary["jira_tickets_created"] += len(jira_keys)

                if config.slack.enabled and result.anomalies:
                    sent = slack_notifier.send_analysis_alerts(result, jira_keys=jira_keys)
                    summary["slack_alerts_sent"] += sent

                summary["total_anomalies"] += len(result.anomalies)
                summary["maintenance"]["diagnoses"].append(diagnosis)
                logger.info(f"  {svc_name}: {len(logs)} errors, {len(result.anomalies)} anomalies")

            except Exception as e:
                logger.exception(f"Error diagnosing {svc_name}: {e}")
                summary["maintenance"]["diagnoses"].append({
                    "service": svc_name,
                    "verdict": "QUERY_FAILED",
                    "error": str(e)[:200],
                })

        kong_count = sum(
            1 for d in summary["maintenance"]["diagnoses"]
            if d.get("verdict") == "KONG_ISSUE"
        )
        summary["maintenance"]["kong_issues"] = kong_count
    else:
        logger.info("✅ All services healthy — maintenance API OK")
        summary["maintenance"]["message"] = "All services healthy"

    # ╔═══════════════════════════════════════════════════════════════╗
    # ║  Phase 2 — Standard preset analysis                          ║
    # ╚═══════════════════════════════════════════════════════════════╝
    analyze_all = os.environ.get("ANALYZE_ALL_SERVICES", "false").lower() == "true"
    
    if analyze_all:
        logger.info("Phase 2: All-services anomaly scan")
        
        # Discover services
        logger.info("Fetching logs to discover active services...")
        all_logs = engine.query(raw="*", time_range=time_range, limit=1000)
        
        if not all_logs:
            logger.warning("No logs found in this window")
        else:
            from collections import Counter
            svc_counter = Counter(log.service for log in all_logs if log.service)
            discovered = svc_counter.most_common()
            logger.info(f"Discovered {len(discovered)} active service(s): {', '.join(s for s, _ in discovered)}")
            
            for svc_name, svc_total in discovered:
                logger.info(f"── Analyzing service: {svc_name} ──")
                try:
                    svc_query = f"service:{svc_name}"
                    svc_logs = [l for l in all_logs if l.service == svc_name]
                    time_from, time_to = parse_time_range(time_range)
                    
                    result = analysis_engine.analyze(
                        logs=svc_logs,
                        query=svc_query,
                        time_from=time_from,
                        time_to=time_to,
                    )
                    
                    preset_summary = {
                        "preset": f"service:{svc_name}",
                        "total_logs": svc_total,
                        "sampled_logs": len(svc_logs),
                        "anomalies": len(result.anomalies),
                        "patterns": len(result.patterns),
                        "error_groups": len(result.error_groups),
                        "trend": result.trends.trend_direction,
                    }
                    
                    # Enhance anomaly descriptions with AI (cap to avoid timeout)
                    error_messages = [l.message for l in svc_logs if l.status in ("error", "critical") and l.message][:30]
                    for anom in result.anomalies[:3]:
                        anom.description = ai_describer.enhance(
                            anomaly_type=anom.anomaly_type.value,
                            description=anom.description,
                            service=svc_name,
                            metric_value=anom.metric_value,
                            expected_value=anom.expected_value,
                            error_logs=error_messages,
                            query=svc_query,
                        )

                    # Notifications
                    jira_keys = {}
                    if result.anomalies:
                        if config.jira.enabled:
                            jira_keys = jira_notifier.create_tickets_from_analysis(result)
                            preset_summary["jira_tickets"] = list(jira_keys.values())
                            summary["jira_tickets_created"] += len(jira_keys)
                        
                        if config.slack.enabled:
                            sent = slack_notifier.send_analysis_alerts(result, jira_keys=jira_keys)
                            preset_summary["slack_alerts"] = sent
                            summary["slack_alerts_sent"] += sent
                            
                        summary["total_anomalies"] += len(result.anomalies)
                    else:
                        logger.info("✅ No anomalies detected")
                        
                    # Reports
                    if s3_uploader and (len(svc_logs) > 0 or result.anomalies):
                        report_error_logs = [
                            {"timestamp": l.timestamp.isoformat(), "service": l.service, "message": l.message}
                            for l in svc_logs if l.status in ("error", "critical")
                        ]
                        html_content = generate_html_report(result, output_path=None, error_logs=report_error_logs)
                        html_content_str = html_content if isinstance(html_content, str) else html_content.read_text()
                        html_result = s3_uploader.upload_report(html_content_str, report_type="html")
                        json_result = s3_uploader.upload_report(generate_json_report(result), report_type="json")
                        
                        preset_summary["html_report"] = html_result.get("presigned_url", "")
                        preset_summary["json_report"] = json_result.get("presigned_url", "")
                        summary["reports_uploaded"].append(preset_summary.get("html_report", ""))
                    
                    summary["presets_analyzed"].append(preset_summary)
                    
                except Exception as e:
                    logger.exception(f"Error analyzing service '{svc_name}': {e}")
                    summary["presets_analyzed"].append({"preset": f"service:{svc_name}", "error": str(e)})

    else:
        logger.info("Phase 2: Standard preset analysis")
        presets_to_run = list(config.presets.keys()) if config.presets else [None]

        for preset_name in presets_to_run:
            logger.info(f"── Analyzing preset: {preset_name or '*'} ──")

            try:
                if preset_name:
                    display_query = engine.resolve_preset(preset_name)
                    display_query = engine._apply_scope(display_query)
                else:
                    display_query = engine._apply_scope("*")

                time_from, time_to = parse_time_range(time_range)

                # Tier 1: Server-side aggregation (non-fatal)
                aggregation = None
                try:
                    logger.info("Tier 1: Running aggregation")
                    aggregation = engine.aggregate(
                        preset=preset_name,
                        raw="*" if not preset_name else None,
                        time_range=time_range,
                        group_by=["service", "status"],
                    )
                    logger.info(f"Aggregation: {aggregation.total} total logs")
                except Exception as agg_err:
                    logger.warning(f"Tier 1 aggregation failed ({agg_err}) — continuing with Tier 2 only")

                # Tier 2: Sample logs
                sample_size = config.analysis.sample_size
                logger.info(f"Tier 2: Fetching up to {sample_size} sample logs")
                logs = engine.query(
                    preset=preset_name,
                    raw="*" if not preset_name else None,
                    time_range=time_range,
                    limit=sample_size,
                )
                logger.info(f"Fetched {len(logs)} logs for detailed analysis")

                # Run analysis
                result = analysis_engine.analyze(
                    logs=logs,
                    query=display_query,
                    time_from=time_from,
                    time_to=time_to,
                    aggregation=aggregation,
                )

                preset_summary = {
                    "preset": preset_name or "*",
                    "total_logs": aggregation.total if aggregation else len(logs),
                    "sampled_logs": len(logs),
                    "anomalies": len(result.anomalies),
                    "patterns": len(result.patterns),
                    "error_groups": len(result.error_groups),
                    "trend": result.trends.trend_direction,
                }

                # Enhance anomaly descriptions with AI (cap to avoid timeout)
                error_messages = [l.message for l in logs if l.status in ("error", "critical") and l.message][:30]
                for anom in result.anomalies[:3]:
                    anom.description = ai_describer.enhance(
                        anomaly_type=anom.anomaly_type.value,
                        description=anom.description,
                        service=anom.service or (preset_name or ""),
                        metric_value=anom.metric_value,
                        expected_value=anom.expected_value,
                        error_logs=error_messages,
                        query=display_query,
                    )

                # Notifications
                jira_keys = {}
                if result.anomalies:
                    logger.info(f"🚨 {len(result.anomalies)} anomalies detected!")

                    if config.jira.enabled:
                        jira_keys = jira_notifier.create_tickets_from_analysis(result)
                        preset_summary["jira_tickets"] = list(jira_keys.values())
                        summary["jira_tickets_created"] += len(jira_keys)
                        logger.info(f"Created {len(jira_keys)} Jira ticket(s)")

                    if config.slack.enabled:
                        sent = slack_notifier.send_analysis_alerts(result, jira_keys=jira_keys)
                        preset_summary["slack_alerts"] = sent
                        summary["slack_alerts_sent"] += sent
                        logger.info(f"Sent {sent} Slack alert(s)")

                    summary["total_anomalies"] += len(result.anomalies)
                else:
                    logger.info("✅ No anomalies detected")

                # Upload reports to S3 (only if there are findings)
                if s3_uploader and (len(logs) > 0 or result.anomalies):
                    report_error_logs = [
                        {"timestamp": l.timestamp.isoformat(), "service": l.service, "message": l.message}
                        for l in logs if l.status in ("error", "critical")
                    ]
                    html_content = generate_html_report(result, output_path=None, error_logs=report_error_logs)
                    if isinstance(html_content, str):
                        html_result = s3_uploader.upload_report(html_content, report_type="html")
                    else:
                        html_content_str = html_content.read_text()
                        html_result = s3_uploader.upload_report(html_content_str, report_type="html")

                    json_content = generate_json_report(result)
                    json_result = s3_uploader.upload_report(json_content, report_type="json")

                    preset_summary["html_report"] = html_result.get("presigned_url", "")
                    preset_summary["json_report"] = json_result.get("presigned_url", "")
                    summary["reports_uploaded"].append(preset_summary.get("html_report", ""))

                    logger.info("Reports uploaded to S3")

                summary["presets_analyzed"].append(preset_summary)

            except Exception as e:
                logger.exception(f"Error analyzing preset '{preset_name}': {e}")
                summary["presets_analyzed"].append({
                    "preset": preset_name or "*",
                    "error": str(e),
                })

    logger.info(
        f"── Lambda complete: "
        f"maintenance={summary['maintenance'].get('state', 'N/A')}, "
        f"{summary['total_anomalies']} anomalies, "
        f"{summary['slack_alerts_sent']} Slack, "
        f"{summary['jira_tickets_created']} Jira ──"
    )

    return summary

