"""Click CLI — dd-logs command-line interface."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

from dd_log_analyzer.analysis.engine import AnalysisEngine
from dd_log_analyzer.client import DatadogLogClient
from dd_log_analyzer.config import load_config
from dd_log_analyzer.notifications.alert_state import AlertStateDB
from dd_log_analyzer.notifications.jira import JiraNotifier
from dd_log_analyzer.notifications.slack import SlackNotifier
from dd_log_analyzer.query.engine import QueryEngine, parse_time_range
from dd_log_analyzer.reporting.console import print_analysis, print_logs
from dd_log_analyzer.reporting.html_report import generate_html_report
from dd_log_analyzer.reporting.json_report import generate_json_report
from dd_log_analyzer.webapp.db import init_db, save_anomaly

# Initialize anomaly history DB
try:
    init_db()
except Exception:
    pass  # DB init is optional — web dashboard just won't have data

console = Console()


def _setup_logging(verbose: bool) -> None:
    """Configure logging with Rich handler."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_time=False)],
    )


def _build_services(config, query_engine, query_str, preset, time_range):
    """Create commonly needed service objects."""
    client = DatadogLogClient(config)
    engine = QueryEngine(client, config)
    analysis = AnalysisEngine(config)
    alert_state = AlertStateDB()
    slack = SlackNotifier(config, alert_state)
    jira = JiraNotifier(config, alert_state)
    return client, engine, analysis, alert_state, slack, jira


# =====================================================================
# Main CLI group
# =====================================================================


@click.group()
@click.option("--profile", default="default", help="Config profile name (default: 'default')")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.pass_context
def cli(ctx: click.Context, profile: str, verbose: bool) -> None:
    """dd-logs — Datadog Log Analyzer Agent.

    Analyze Datadog logs with pattern detection, anomaly surfacing,
    error correlation, and trend analysis. Alerts via Slack and Jira.
    """
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(profile=profile)
    ctx.obj["verbose"] = verbose


# =====================================================================
# query — search and display logs
# =====================================================================


@cli.command()
@click.argument("query", default="*")
@click.option("--time", "-t", "time_range", default="last 1h", help="Time range (e.g. 'last 1h', 'last 30m')")
@click.option("--limit", "-l", default=100, help="Max logs to display")
@click.option("--preset", "-p", default=None, help="Use a saved query preset")
@click.pass_context
def query(ctx: click.Context, query: str, time_range: str, limit: int, preset: str | None) -> None:
    """Search and display Datadog logs.

    \b
    Examples:
      dd-logs query "env:prod service:aggregatoradapter status:error"
      dd-logs query --preset kong-errors --time "last 6h"
      dd-logs query '*' --time "last 15m" --limit 50
    """
    config = ctx.obj["config"]
    client = DatadogLogClient(config)
    engine = QueryEngine(client, config)

    with console.status("[cyan]Querying Datadog logs...[/cyan]"):
        logs = engine.query(
            raw=query if not preset else None,
            preset=preset,
            time_range=time_range,
            limit=limit,
        )

    if not logs:
        console.print("[yellow]No logs found matching the query.[/yellow]")
        return

    print_logs(logs, limit=limit)


# =====================================================================
# analyze — run all analyzers
# =====================================================================


@cli.command()
@click.argument("query", default="*")
@click.option("--time", "-t", "time_range", default="last 1h", help="Time range")
@click.option("--preset", "-p", default=None, help="Use a saved query preset")
@click.option("--notify-slack", is_flag=True, help="Send Slack alerts for anomalies")
@click.option("--create-jira", is_flag=True, help="Create Jira tickets for anomalies")
@click.option("--format", "-f", "output_format", type=click.Choice(["console", "json", "html"]), default="console")
@click.option("--output", "-o", "output_path", default=None, help="Output file path (for json/html)")
@click.pass_context
def analyze(
    ctx: click.Context,
    query: str,
    time_range: str,
    preset: str | None,
    notify_slack: bool,
    create_jira: bool,
    output_format: str,
    output_path: str | None,
) -> None:
    """Run full analysis on Datadog logs.

    Uses a two-tier approach:
    1. Aggregation API (Tier 1) — server-side analysis of ALL logs
    2. Targeted search (Tier 2) — sample logs for pattern detection

    \b
    Examples:
      dd-logs analyze "env:prod service:aggregatoradapter" --time "last 1h"
      dd-logs analyze --preset kong-errors --notify-slack --create-jira
      dd-logs analyze "status:error" --format html --output report.html
    """
    config = ctx.obj["config"]
    client = DatadogLogClient(config)
    engine = QueryEngine(client, config)
    analysis_engine = AnalysisEngine(config)
    alert_state = AlertStateDB()

    # Resolve the query string for display
    if preset:
        resolved_query = engine.resolve_preset(preset)
        resolved_query = engine._apply_scope(resolved_query)
    else:
        resolved_query = engine._apply_scope(query)

    time_from, time_to = parse_time_range(time_range)

    # Tier 1: Server-side aggregation (covers ALL logs)
    aggregation = None
    try:
        with console.status("[cyan]Tier 1: Running server-side aggregation...[/cyan]"):
            aggregation = engine.aggregate(
                raw=query if not preset else None,
                preset=preset,
                time_range=time_range,
                group_by=["service", "status"],
            )
        console.print(f"[green]✓[/green] Aggregation complete — covers all logs server-side")
    except Exception as e:
        console.print(f"[yellow]⚠️  Tier 1 skipped ({e.__class__.__name__}) — continuing with Tier 2 only[/yellow]")

    # Tier 2: Fetch sample logs for detailed analysis
    sample_size = config.analysis.sample_size
    with console.status(f"[cyan]Tier 2: Fetching {sample_size:,} sample logs...[/cyan]"):
        logs = engine.query(
            raw=query if not preset else None,
            preset=preset,
            time_range=time_range,
            limit=sample_size,
        )
    console.print(f"[green]✓[/green] Fetched {len(logs):,} logs for detailed analysis")

    # Run analysis
    with console.status("[cyan]Running analysis (patterns, anomalies, errors, trends)...[/cyan]"):
        result = analysis_engine.analyze(
            logs=logs,
            query=resolved_query,
            time_from=time_from,
            time_to=time_to,
            aggregation=aggregation,
        )

    # Persist anomalies to dashboard DB
    for anom in result.anomalies:
        try:
            save_anomaly(
                timestamp=result.time_to.isoformat(),
                service=anom.service,
                anomaly_type=anom.anomaly_type.value,
                severity=anom.severity.value,
                description=anom.description,
                metric_value=anom.metric_value,
                expected_value=anom.expected_value,
                query=resolved_query,
            )
        except Exception:
            pass

    # Notifications
    jira_keys: dict[str, str] = {}
    if create_jira and result.anomalies:
        jira_notifier = JiraNotifier(config, alert_state)
        with console.status("[cyan]Creating Jira tickets...[/cyan]"):
            jira_keys = jira_notifier.create_tickets_from_analysis(result)
        if jira_keys:
            console.print(f"[green]✓[/green] Created {len(jira_keys)} Jira ticket(s)")

    if notify_slack and result.anomalies:
        slack_notifier = SlackNotifier(config, alert_state)
        with console.status("[cyan]Sending Slack alerts...[/cyan]"):
            sent = slack_notifier.send_analysis_alerts(result, jira_keys=jira_keys)
        console.print(f"[green]✓[/green] Sent {sent} Slack alert(s)")

    # Output
    if output_format == "json":
        output_path = output_path or "analysis_report.json"
        generate_json_report(result, output_path)
        console.print(f"[green]✓[/green] JSON report saved to {output_path}")
    elif output_format == "html":
        output_path = output_path or "analysis_report.html"
        report_error_logs = [
            {"timestamp": l.timestamp.isoformat(), "service": l.service, "message": l.message}
            for l in logs if l.status in ("error", "critical")
        ]
        generate_html_report(result, output_path, error_logs=report_error_logs)
        console.print(f"[green]✓[/green] HTML report saved to {output_path}")
    else:
        print_analysis(result)

    alert_state.close()


# =====================================================================
# report — generate report without terminal output
# =====================================================================


@cli.command()
@click.argument("query", default="*")
@click.option("--time", "-t", "time_range", default="last 1h", help="Time range")
@click.option("--preset", "-p", default=None, help="Use a saved query preset")
@click.option("--format", "-f", "output_format", type=click.Choice(["json", "html"]), default="html")
@click.option("--output", "-o", "output_path", required=True, help="Output file path")
@click.pass_context
def report(
    ctx: click.Context,
    query: str,
    time_range: str,
    preset: str | None,
    output_format: str,
    output_path: str,
) -> None:
    """Generate an analysis report (HTML or JSON).

    \b
    Examples:
      dd-logs report "env:prod" --time "last 24h" --output report.html
      dd-logs report --preset api-5xx --format json --output errors.json
    """
    # Delegate to analyze with the right format
    ctx.invoke(
        analyze,
        query=query,
        time_range=time_range,
        preset=preset,
        notify_slack=False,
        create_jira=False,
        output_format=output_format,
        output_path=output_path,
    )


# =====================================================================
# watch — continuous monitoring with polling + maintenance check
# =====================================================================

_DEFAULT_MAINTENANCE_URL = (
    "https://api.prod.jaja.finance/svc/v3/services/maintenance"
    "?platform=IOS&brand=jaja"
)


@cli.command()
@click.argument("query", default="*")
@click.option("--interval", "-i", default=60, help="Polling interval in seconds (default: 60)")
@click.option("--preset", "-p", default=None, help="Use a saved query preset")
@click.option("--notify-slack/--no-slack", default=True, help="Send Slack alerts (default: yes)")
@click.option("--create-jira/--no-jira", default=True, help="Create Jira tickets (default: yes)")
@click.option("--all-services", is_flag=True, default=False, help="Discover and analyze ALL services individually")
@click.option(
    "--maintenance-url",
    default=_DEFAULT_MAINTENANCE_URL,
    help="Maintenance API URL to check each cycle.",
)
@click.pass_context
def watch(
    ctx: click.Context,
    query: str,
    interval: int,
    preset: str | None,
    notify_slack: bool,
    create_jira: bool,
    all_services: bool,
    maintenance_url: str,
) -> None:
    """Continuously monitor Datadog logs AND service health.

    Every cycle:
      1. Calls the maintenance API — if services are unhealthy,
         drills into Datadog for each one (Kong attribution if no DD errors).
      2. Runs standard anomaly analysis on the main query/preset.
         With --all-services: discovers all services and analyzes each.

    \b
    Examples:
      dd-logs watch "env:prod" --interval 60
      dd-logs watch --preset kong-errors --interval 120
      dd-logs watch --all-services --interval 60
      dd-logs watch "status:error" --interval 300 --maintenance-url "https://..."
    """
    import httpx
    from rich.panel import Panel
    from rich.table import Table

    # Suppress noisy httpx/httpcore debug logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    config = ctx.obj["config"]
    client = DatadogLogClient(config)
    engine = QueryEngine(client, config)
    analysis_engine = AnalysisEngine(config)
    alert_state = AlertStateDB()
    slack_notifier = SlackNotifier(config, alert_state) if notify_slack else None
    jira_notifier = JiraNotifier(config, alert_state) if create_jira else None

    # Resolve for display
    if all_services:
        display_query = "* (all services)"
    elif preset:
        display_query = engine.resolve_preset(preset)
    else:
        display_query = query

    console.print(f"[bold cyan]👁 Watch Mode[/bold cyan]")
    console.print(f"  Query: [cyan]{display_query}[/cyan]")
    console.print(f"  Interval: {interval}s")
    if all_services:
        console.print(f"  Mode: [bold magenta]🔍 All Services Discovery[/bold magenta]")
    console.print(f"  Maintenance: [cyan]{maintenance_url[:60]}...[/cyan]")
    console.print(f"  Slack: {'✅' if notify_slack else '❌'} | Jira: {'✅' if create_jira else '❌'}")
    console.print(f"  Press Ctrl+C to stop\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            time_range_str = f"last {interval * 2}s" if interval < 60 else f"last {max(interval // 60, 1) * 2}m"

            console.print(f"[dim]──── Cycle {cycle} ({time_range_str}) ────[/dim]")

            # ╔═══════════════════════════════════════════════╗
            # ║  Phase 1 — Maintenance API health check       ║
            # ╚═══════════════════════════════════════════════╝
            try:
                resp = httpx.get(
                    maintenance_url,
                    headers={"x-jaja-version": "v2.0"},
                    timeout=10,
                )
                resp.raise_for_status()
                maint_data = resp.json()
            except Exception as maint_err:
                console.print(f"  [dim]⚠️  Maintenance API unreachable: {maint_err}[/dim]")
                maint_data = {"state": "OK", "maintenance": []}

            maint_state = maint_data.get("state", "OK")
            maint_services = maint_data.get("maintenance", [])

            if maint_services:
                console.print(f"\n[cyan]Phase 1:[/cyan] Checking maintenance API...")

                time_from, time_to = parse_time_range(time_range_str)
                findings: list[dict] = []

                for idx, svc in enumerate(maint_services, 1):
                    if isinstance(svc, str):
                        svc_name = svc
                    elif isinstance(svc, dict):
                        svc_name = svc.get("name", svc.get("service", str(svc)))
                    else:
                        svc_name = str(svc)

                    dd_query = f"service:{svc_name} status:(error OR critical)"

                    try:
                        # Aggregation
                        aggregation = None
                        try:
                            aggregation = engine.aggregate(
                                raw=dd_query,
                                time_range=time_range_str,
                                group_by=["service", "status"],
                            )
                        except Exception:
                            pass

                        # Sample logs
                        logs = engine.query(raw=dd_query, time_range=time_range_str, limit=500)

                        if not logs and (not aggregation or aggregation.total == 0):
                            # ── No DD errors → Kong ──
                            inner_panel = Panel(
                                f"[bold]{svc_name} — Kong Issue[/bold]\n"
                                f"[yellow]⚠️[/yellow]  No errors in Datadog for [cyan]{svc_name}[/cyan]\n"
                                f"[red]→ Root Cause: Kong Gateway[/red]\n"
                                f"[dim]Check Kong routes, upstreams, and health-check config.[/dim]",
                                border_style="yellow",
                                padding=(0, 2),
                            )
                            outer_panel = Panel(
                                inner_panel,
                                title=f"[bold red]🏥 MAINTENANCE WINDOW[/bold red] [dim]—[/dim] [red]{len(maint_services)} unhealthy service(s)[/red]",
                                border_style="red",
                                padding=(1, 1),
                            )
                            console.print(outer_panel)
                            findings.append({
                                "service": svc_name, "errors": 0,
                                "verdict": "🔀 KONG ISSUE",
                                "detail": "No DD errors — Kong routing issue",
                            })

                            # Slack alert for Kong issue
                            if slack_notifier:
                                try:
                                    slack_notifier._send_webhook({
                                        "text": (
                                            f"🔀 *KONG ISSUE — {svc_name}*\n"
                                            f"Maintenance API reports unhealthy, "
                                            f"but Datadog has 0 errors.\n"
                                            f"→ Check Kong routes & upstreams."
                                        ),
                                    })
                                except Exception:
                                    pass

                            continue

                        # ── Analyse ──
                        result = analysis_engine.analyze(
                            logs=logs, query=dd_query,
                            time_from=time_from, time_to=time_to,
                            aggregation=aggregation,
                        )

                        total_errors = len(logs)
                        sev_color = "red" if total_errors > 50 else "yellow" if total_errors > 0 else "green"

                        # Diagnostic panel
                        diag_lines = [
                            f"  [bold]Errors:[/bold]       [red]{total_errors:,}[/red]",
                            f"  [bold]Anomalies:[/bold]    {len(result.anomalies)}",
                            f"  [bold]Error Groups:[/bold] {len(result.error_groups)}",
                            f"  [bold]Trend:[/bold]        {result.trends.trend_direction}",
                        ]
                        console.print(Panel(
                            "\n".join(diag_lines),
                            title=f"[bold {sev_color}]{svc_name} — {total_errors:,} errors[/bold {sev_color}]",
                            border_style=sev_color,
                            padding=(1, 2),
                        ))

                        # Top errors table
                        if result.error_groups:
                            eg_table = Table(
                                title=f"{svc_name} — Top Errors",
                                show_header=True, header_style="bold dim",
                                border_style="dim", title_style="bold red",
                                expand=True,
                            )
                            eg_table.add_column("#", width=3, style="dim")
                            eg_table.add_column("Count", justify="right", style="bold")
                            eg_table.add_column("Error", max_width=70)
                            eg_table.add_column("Root Cause", style="yellow")

                            for j, eg in enumerate(result.error_groups[:5], 1):
                                sample = eg.sample_messages[0][:70] if eg.sample_messages else "—"
                                root = eg.root_cause_candidate or "—"
                                eg_table.add_row(str(j), f"{eg.count:,}", sample, root)
                            console.print(eg_table)

                        # Jira + Slack for service anomalies
                        jira_keys: dict[str, str] = {}
                        if jira_notifier and result.anomalies:
                            jira_keys = jira_notifier.create_tickets_from_analysis(result)
                        if slack_notifier and result.anomalies:
                            slack_notifier.send_analysis_alerts(result, jira_keys=jira_keys)

                        findings.append({
                            "service": svc_name, "errors": total_errors,
                            "verdict": "🚨 SERVICE ERROR",
                            "detail": f"{len(result.error_groups)} groups",
                        })

                    except Exception as svc_err:
                        console.print(f"  [red]❌ {svc_name}: {svc_err}[/red]")
                        findings.append({
                            "service": svc_name, "errors": -1,
                            "verdict": "❌ QUERY FAILED",
                            "detail": str(svc_err)[:60],
                        })

                # Maintenance summary table
                summary_table = Table(
                    title="🏥 Maintenance Diagnosis",
                    show_header=True, header_style="bold",
                    title_style="bold cyan", expand=True,
                )
                summary_table.add_column("Service", style="cyan")
                summary_table.add_column("Errors", justify="right")
                summary_table.add_column("Verdict", min_width=18)
                summary_table.add_column("Details")

                for f in findings:
                    es = "red" if f["errors"] > 0 else "green" if f["errors"] == 0 else "dim"
                    summary_table.add_row(
                        f["service"],
                        f"[{es}]{f['errors']:,}[/{es}]" if f["errors"] >= 0 else "[dim]N/A[/dim]",
                        f["verdict"],
                        f["detail"],
                    )
                console.print(summary_table)

                kong_count = sum(1 for f in findings if "KONG" in f["verdict"])
                if kong_count:
                    console.print(
                        f"\n[bold yellow]⚠️  {kong_count} service(s) → Kong Gateway issue[/bold yellow]"
                    )
            else:
                console.print(f"\n[cyan]Phase 1:[/cyan] Checking maintenance API... [green]OK[/green] — all services healthy")

            # ╔═══════════════════════════════════════════════╗
            # ║  Phase 2 — Anomaly analysis                   ║
            # ╚═══════════════════════════════════════════════╝
            try:
                time_from, time_to = parse_time_range(time_range_str)

                if all_services:
                    # ── All-services discovery mode ──
                    console.print(f"\n[cyan]Phase 2:[/cyan] Discovering services...")
                    all_logs = engine.query(
                        raw="*",
                        time_range=time_range_str,
                        limit=1000,
                    )

                    if not all_logs:
                        console.print(f"  [yellow]No logs found in this window[/yellow]")
                    else:
                        # Extract unique service names and counts
                        from collections import Counter
                        svc_counter = Counter(log.service for log in all_logs if log.service)
                        discovered = svc_counter.most_common()  # sorted by count desc

                        svc_list = ", ".join(s for s, _ in discovered[:5])
                        extra = f"..." if len(discovered) > 5 else ""
                        console.print(f"[green]Found {len(discovered)} service(s):[/green] [dim]{svc_list}{extra}[/dim]")

                        # Step 2: Analyze each service individually
                        svc_results: list[dict] = []
                        for svc_name, svc_total in discovered:
                            svc_query = f"service:{svc_name}"
                            try:
                                # Filter from already-fetched logs (no extra API call)
                                svc_logs = [l for l in all_logs if l.service == svc_name]
                                svc_result = analysis_engine.analyze(
                                    logs=svc_logs,
                                    query=svc_query,
                                    time_from=time_from,
                                    time_to=time_to,
                                )

                                error_count = sum(1 for l in svc_logs if l.status in ("error", "critical"))
                                svc_results.append({
                                    "service": svc_name,
                                    "total": svc_total,
                                    "errors": error_count,
                                    "anomalies": len(svc_result.anomalies),
                                    "error_groups": len(svc_result.error_groups),
                                    "patterns": len(svc_result.patterns),
                                    "trend": svc_result.trends.trend_direction,
                                    "result": svc_result,
                                })

                                # Show anomaly details per service
                                if svc_result.anomalies:
                                    # Persist to dashboard DB
                                    for anom in svc_result.anomalies:
                                        try:
                                            save_anomaly(
                                                timestamp=svc_result.time_to.isoformat(),
                                                service=anom.service or svc_name,
                                                anomaly_type=anom.anomaly_type.value,
                                                severity=anom.severity.value,
                                                description=anom.description,
                                                metric_value=anom.metric_value,
                                                expected_value=anom.expected_value,
                                                query=svc_query,
                                            )
                                        except Exception:
                                            pass
                                    for anom in svc_result.anomalies:
                                        is_critical = anom.severity.value == "critical"
                                        sev_color = "red" if is_critical else "yellow"
                                        sev_label = "🚨 CRITICAL" if is_critical else "⚠️ WARNING"
                                        type_label = anom.anomaly_type.value.upper().replace("_", " ")
                                        anom_lines = [
                                            f"[bold {sev_color}]{sev_label}[/bold {sev_color}]  [dim]│[/dim]  [bold red]{type_label}[/bold red]",
                                            "",
                                            f"[dim]Description:[/dim] {anom.description}",
                                            "",
                                            f"[bold]Actual[/bold]    [bold]Expected[/bold]",
                                            f"[bold {sev_color}]{anom.metric_value:,.0f}[/bold {sev_color}] logs   [bold green]~{anom.expected_value:,.0f}[/bold green] logs",
                                        ]
                                        if anom.window_start and anom.window_end:
                                            anom_lines.append(f"\n[dim]Window:[/dim]  {anom.window_start.strftime('%H:%M:%S')} → {anom.window_end.strftime('%H:%M:%S')}")
                                        console.print(Panel(
                                            "\n".join(anom_lines),
                                            title=f"[bold]{svc_name}[/bold]",
                                            border_style=sev_color,
                                            padding=(1, 2),
                                        ))

                                    # Notify for this service
                                    jira_keys = {}
                                    if jira_notifier:
                                        jira_keys = jira_notifier.create_tickets_from_analysis(svc_result)
                                        jira_ids = ", ".join(jira_keys.values()) if jira_keys else None
                                    else:
                                        jira_ids = None
                                    if slack_notifier:
                                        slack_notifier.send_analysis_alerts(svc_result, jira_keys=jira_keys)

                                    # Confirmation line
                                    parts = ["[green]✓[/green] [bold]Slack alert sent[/bold]"]
                                    if jira_ids:
                                        parts.append(f"[green]✓[/green] [bold]Jira {jira_ids}[/bold] created")
                                    console.print("  ".join(parts))

                            except Exception as svc_err:
                                svc_results.append({
                                    "service": svc_name, "total": svc_total,
                                    "errors": -1, "anomalies": -1,
                                    "error_groups": 0, "patterns": 0,
                                    "trend": "error", "result": None,
                                })

                        # Step 3: Summary table
                        summary = Table(
                            title="🔍 All Services — Anomaly Scan",
                            show_header=True, header_style="bold dim",
                            title_style="bold cyan", expand=True,
                            show_lines=True, border_style="dim",
                        )
                        summary.add_column("Service", style="cyan", min_width=20)
                        summary.add_column("Total", justify="right")
                        summary.add_column("Errors", justify="right")
                        summary.add_column("Anomalies", justify="right")
                        summary.add_column("Trend")
                        summary.add_column("Status", justify="center")

                        total_anomalies = 0
                        for sr in svc_results:
                            err_style = "red bold" if sr["errors"] > 0 else "green" if sr["errors"] == 0 else "dim"
                            anom_style = "red bold" if sr["anomalies"] > 0 else "green" if sr["anomalies"] == 0 else "dim"
                            trend_map = {"increasing": "📈 incr.", "decreasing": "📉 decr.", "stable": "→ stable"}
                            trend_text = trend_map.get(sr["trend"], "? " + sr["trend"])
                            trend_style = "red" if sr["trend"] == "increasing" else "green" if sr["trend"] == "decreasing" else "dim"
                            status = "🚨" if sr["anomalies"] > 0 else "✅" if sr["anomalies"] == 0 else "❌"
                            total_anomalies += max(sr["anomalies"], 0)

                            summary.add_row(
                                sr["service"],
                                f"{sr['total']:,}",
                                f"[{err_style}]{sr['errors']:,}[/{err_style}]" if sr["errors"] >= 0 else "[dim]N/A[/dim]",
                                f"[{anom_style}]{sr['anomalies']}[/{anom_style}]" if sr["anomalies"] >= 0 else "[dim]N/A[/dim]",
                                f"[{trend_style}]{trend_text}[/{trend_style}]",
                                status,
                            )
                        console.print(summary)

                        # Summary banner
                        if total_anomalies:
                            anomaly_services = sum(1 for s in svc_results if s["anomalies"] > 0)
                            console.print(Panel(
                                f"[bold red]🚨 {total_anomalies} total anomaly(ies) across {anomaly_services} service(s)[/bold red]",
                                border_style="red",
                                padding=(0, 1),
                            ))
                        else:
                            console.print(Panel(
                                f"[bold green]✅ All clear — {len(discovered)} services scanned, no anomalies[/bold green]",
                                border_style="green",
                                padding=(0, 1),
                            ))

                else:
                    # ── Standard single-query mode ──
                    # Tier 1: Server-side aggregation
                    aggregation = None
                    try:
                        aggregation = engine.aggregate(
                            raw=query if not preset else None,
                            preset=preset,
                            time_range=time_range_str,
                            group_by=["service", "status"],
                        )
                    except Exception:
                        pass

                    # Always fetch sample logs for Tier 2 analysis
                    logs = engine.query(
                        raw=query if not preset else None,
                        preset=preset,
                        time_range=time_range_str,
                        limit=1000,
                    )

                    # Run full two-tier analysis (Tier 1 + Tier 2)
                    result = analysis_engine.analyze(
                        logs=logs,
                        query=display_query,
                        time_from=time_from,
                        time_to=time_to,
                        aggregation=aggregation,
                    )

                    if result.anomalies:
                        console.print(f"  [red]🚨 {len(result.anomalies)} anomaly(ies) detected![/red]")

                        # Persist to dashboard DB
                        for anom in result.anomalies:
                            try:
                                save_anomaly(
                                    timestamp=result.time_to.isoformat(),
                                    service=anom.service,
                                    anomaly_type=anom.anomaly_type.value,
                                    severity=anom.severity.value,
                                    description=anom.description,
                                    metric_value=anom.metric_value,
                                    expected_value=anom.expected_value,
                                    query=query if not preset else engine.resolve_preset(preset),
                                )
                            except Exception:
                                pass

                        # Notify
                        jira_keys = {}
                        if jira_notifier and result.anomalies:
                            jira_keys = jira_notifier.create_tickets_from_analysis(result)
                            if jira_keys:
                                console.print(f"  [green]✓ {len(jira_keys)} Jira ticket(s) created[/green]")

                        if slack_notifier and result.anomalies:
                            sent = slack_notifier.send_analysis_alerts(result, jira_keys=jira_keys)
                            if sent:
                                console.print(f"  [green]✓ {sent} Slack alert(s) sent[/green]")

                        # ── Rich anomaly panels ──
                        for i, anom in enumerate(result.anomalies, 1):
                            is_critical = anom.severity.value == "critical"
                            sev_color = "red" if is_critical else "yellow"
                            sev_icon = "🚨" if is_critical else "⚠️"
                            sev_label = "CRITICAL" if is_critical else "WARNING"

                            lines: list[str] = []
                            lines.append(f"[bold {sev_color}]{sev_icon} {sev_label}[/bold {sev_color}]  │  [bold]{anom.anomaly_type.value.upper().replace('_', ' ')}[/bold]")
                            lines.append("")
                            lines.append(f"  [bold]Description:[/bold]  {anom.description}")
                            if anom.service:
                                lines.append(f"  [bold]Service:[/bold]      {anom.service}")
                            lines.append(f"  [bold]Actual:[/bold]       [bold {sev_color}]{anom.metric_value:,.0f}[/bold {sev_color}] logs")
                            lines.append(f"  [bold]Expected:[/bold]    ~{anom.expected_value:,.0f} logs")
                            if anom.zscore:
                                lines.append(f"  [bold]Z-Score:[/bold]     [{sev_color}]{anom.zscore:+.2f}[/{sev_color}] (threshold: ±{config.analysis.anomaly_zscore_threshold:.2f})")
                            if anom.window_start and anom.window_end:
                                lines.append(f"  [bold]Window:[/bold]      {anom.window_start.strftime('%H:%M:%S')} → {anom.window_end.strftime('%H:%M:%S')}")
                            if anom.details.get("services"):
                                lines.append(f"  [bold]Affected:[/bold]    {', '.join(anom.details['services'][:5])}")

                            anom_fp = f"{anom.anomaly_type.value}:{anom.service or 'n/a'}"
                            if jira_keys.get(anom_fp):
                                lines.append(f"  [bold]Jira:[/bold]        [cyan]{jira_keys[anom_fp]}[/cyan]")

                            console.print(Panel(
                                "\n".join(lines),
                                title=f"[bold {sev_color}]Anomaly {i}/{len(result.anomalies)}[/bold {sev_color}]",
                                border_style=sev_color,
                                padding=(1, 2),
                            ))

                        # ── Top error groups ──
                        if result.error_groups:
                            eg_table = Table(
                                title="Top Error Groups",
                                show_header=True, header_style="bold dim",
                                border_style="dim", title_style="bold cyan",
                                pad_edge=False, expand=True,
                            )
                            eg_table.add_column("#", style="dim", width=3)
                            eg_table.add_column("Service", style="cyan")
                            eg_table.add_column("Count", justify="right", style="bold")
                            eg_table.add_column("Error", max_width=60)
                            eg_table.add_column("Root Cause", style="yellow")

                            for j, eg in enumerate(result.error_groups[:5], 1):
                                services_str = ", ".join(eg.services[:2])
                                sample = eg.sample_messages[0][:60] if eg.sample_messages else "—"
                                root = eg.root_cause_candidate or "—"
                                eg_table.add_row(str(j), services_str, f"{eg.count:,}", sample, root)
                            console.print(eg_table)

                        # ── Top patterns ──
                        if result.patterns:
                            console.print(f"\n  [bold cyan]Top Patterns:[/bold cyan]")
                            for j, pat in enumerate(result.patterns[:3], 1):
                                svcs = ", ".join(list(pat.services.keys())[:2])
                                if pat.sample_messages:
                                    raw = pat.sample_messages[0]
                                    import re
                                    cleaned = re.sub(r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?", "", raw)
                                    cleaned = re.sub(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "", cleaned)
                                    cleaned = re.sub(r"\*\d+", "", cleaned)
                                    cleaned = re.sub(r"\b\d{5,}\b", "", cleaned)
                                    cleaned = re.sub(r"\s+", " ", cleaned).strip()
                                    label = cleaned[:60].strip()
                                    if not label:
                                        label = raw[:60]
                                else:
                                    label = pat.template[:60]
                                pct = f"{pat.percentage:.1f}%" if pat.percentage else ""
                                console.print(f"    {j}. [bold]{svcs}[/bold] — {label} [dim]({pat.count:,}x{', ' + pct if pct else ''})[/dim]")
                            console.print()

                    else:
                        total = aggregation.total if aggregation else len(logs)
                        console.print(f"  [green]✅ All clear[/green] — {total:,} logs, no anomalies")

            except Exception as e:
                console.print(f"  [red]Error: {e}[/red]")
                logging.getLogger(__name__).exception("Watch cycle error")

            time.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Watch mode stopped.[/yellow]")
    finally:
        alert_state.close()


# =====================================================================
# health — verify connectivity
# =====================================================================


@cli.command()
@click.pass_context
def health(ctx: click.Context) -> None:
    """Verify Datadog API connectivity and configuration.

    \b
    Example:
      dd-logs health
    """
    config = ctx.obj["config"]
    client = DatadogLogClient(config)

    with console.status("[cyan]Checking Datadog API connectivity...[/cyan]"):
        result = client.health_check()

    if result["status"] == "ok":
        console.print("[green]✅ Datadog API — Connected[/green]")
        console.print(f"   Site: {result['site']}")
        console.print(f"   Index: {result['index']}")
    else:
        console.print(f"[red]❌ Datadog API — Error: {result.get('error', 'Unknown')}[/red]")
        sys.exit(1)

    if config.slack.webhook_url:
        console.print("[green]✅ Slack — Webhook configured[/green]")
    else:
        console.print("[yellow]⚠️  Slack — No webhook URL configured[/yellow]")

    if config.jira.base_url and config.jira.email and config.jira.api_token:
        console.print("[green]✅ Jira — Configured[/green]")
        console.print(f"   URL: {config.jira.base_url}")
        console.print(f"   Project: {config.jira.project_key}")
    else:
        console.print("[yellow]⚠️  Jira — Not fully configured[/yellow]")

    if config.presets:
        console.print(f"\n[cyan]📋 Active Presets ({len(config.presets)}):[/cyan]")
        for name, preset_item in config.presets.items():
            console.print(f"   [bold]{name}[/bold] — {preset_item.description or preset_item.query[:60]}")

    if config.scope.env:
        console.print(f"\n[cyan]🔒 Global Scope:[/cyan] env:{config.scope.env}")


def main() -> None:
    """Entry point for dd-logs CLI."""
    cli()


if __name__ == "__main__":
    main()
