"""Slack notifier — Block Kit webhook messages with Datadog deep-links."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from urllib.parse import quote

import httpx

from dd_log_analyzer.config import AppConfig
from dd_log_analyzer.models.log_entry import Alert, AlertSeverity, AnalysisResult
from dd_log_analyzer.notifications.alert_state import AlertStateDB

logger = logging.getLogger(__name__)

# Severity emoji mapping
_EMOJI = {
    AlertSeverity.CRITICAL: "🚨",
    AlertSeverity.WARNING: "⚠️",
    AlertSeverity.INFO: "ℹ️",
}


def _build_datadog_link(query: str, site: str = "datadoghq.com") -> str:
    """Build a Datadog Log Explorer deep-link with the query pre-filled."""
    encoded_query = quote(query)
    return f"https://app.{site}/logs?query={encoded_query}"


def _build_slack_blocks(alert: Alert, jira_key: str | None = None) -> list[dict]:
    """Build Slack Block Kit blocks for a rich alert message."""
    emoji = _EMOJI.get(alert.severity, "ℹ️")
    severity_label = alert.severity.value.upper()

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} [{severity_label}] {alert.summary}",
                "emoji": True,
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": alert.description,
            },
        },
    ]

    # Details section
    fields = []
    if alert.service:
        fields.append({"type": "mrkdwn", "text": f"*Service:*\n`{alert.service}`"})
    fields.append({"type": "mrkdwn", "text": f"*Type:*\n{alert.alert_type.value}"})
    fields.append({"type": "mrkdwn", "text": f"*Time:*\n{alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}"})

    if jira_key:
        fields.append({"type": "mrkdwn", "text": f"*Jira:*\n`{jira_key}`"})

    if fields:
        blocks.append({"type": "section", "fields": fields})

    # Action buttons
    actions = []
    if alert.datadog_link:
        actions.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔍 View in Datadog"},
                "url": alert.datadog_link,
                "style": "primary",
            }
        )

    if actions:
        blocks.append({"type": "actions", "elements": actions})

    # Footer
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"dd-log-analyzer | Fingerprint: `{alert.fingerprint[:8]}...`",
                }
            ],
        }
    )

    return blocks


class SlackNotifier:
    """Sends formatted Slack messages via Incoming Webhook."""

    def __init__(self, config: AppConfig, alert_state: AlertStateDB | None = None):
        self._config = config
        self._webhook_url = config.slack.webhook_url
        self._alert_state = alert_state

    def _make_fingerprint(self, alert: Alert) -> str:
        """Generate dedup fingerprint for an alert."""
        raw = f"{alert.alert_type.value}|{alert.service or ''}|{alert.summary}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def send_alert(
        self,
        alert: Alert,
        jira_key: str | None = None,
    ) -> bool:
        """Send a Slack alert if not recently sent (dedup check).

        Args:
            alert: The alert to send.
            jira_key: Optional Jira ticket key to include in the message.

        Returns:
            True if the message was sent, False if skipped (dedup) or failed.
        """
        if not self._webhook_url:
            logger.warning("Slack webhook URL not configured, skipping notification")
            return False

        # Build fingerprint
        fingerprint = alert.fingerprint or self._make_fingerprint(alert)

        # Check dedup
        if self._alert_state:
            if not self._alert_state.should_alert(fingerprint, self._config.alerts.cooldown_minutes):
                logger.info(f"Skipping Slack alert (dedup): {alert.summary}")
                return False

        # Build Datadog link
        if not alert.datadog_link and alert.datadog_query:
            alert.datadog_link = _build_datadog_link(alert.datadog_query, self._config.datadog.site)

        # Build and send message
        blocks = _build_slack_blocks(alert, jira_key=jira_key)
        payload = {"blocks": blocks}

        if self._config.slack.channel:
            payload["channel"] = self._config.slack.channel

        try:
            response = httpx.post(
                self._webhook_url,
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()

            # Record in alert state
            if self._alert_state:
                self._alert_state.record_alert(fingerprint, jira_key=jira_key)

            logger.info(f"Slack alert sent: {alert.summary}")
            return True
        except httpx.HTTPError as e:
            logger.error(f"Failed to send Slack alert: {e}")
            return False

    def send_analysis_alerts(
        self,
        result: AnalysisResult,
        jira_keys: dict[str, str] | None = None,
    ) -> int:
        """Send Slack alerts for all anomalies in an analysis result.

        Args:
            result: The analysis result containing anomalies.
            jira_keys: Optional map of fingerprint -> Jira ticket key.

        Returns:
            Number of alerts actually sent.
        """
        if not result.anomalies:
            return 0

        sent_count = 0
        jira_keys = jira_keys or {}

        for anomaly in result.anomalies:
            # Build a fingerprint that includes query + affected services
            # but NOT the description (which contains dynamic counts that change every run)
            affected_services = ",".join(sorted(anomaly.details.get("services", [])))
            service_id = anomaly.service or affected_services or ""
            fingerprint = hashlib.sha256(
                f"{anomaly.anomaly_type.value}|{service_id}|{result.query}".encode()
            ).hexdigest()[:16]

            # Use affected services as the service name for the alert if not set
            alert_service = anomaly.service or affected_services or None

            alert = Alert(
                alert_type=anomaly.anomaly_type,
                severity=anomaly.severity,
                service=alert_service,
                summary=anomaly.description[:120],
                description=anomaly.description,
                fingerprint=fingerprint,
                datadog_query=result.query,
            )

            jira_key = jira_keys.get(fingerprint)
            if self.send_alert(alert, jira_key=jira_key):
                sent_count += 1

        return sent_count
