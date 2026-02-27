"""Jira ticket creator — auto-creates tickets via Jira REST API when anomalies are found."""

from __future__ import annotations

import hashlib
import logging
from base64 import b64encode
from datetime import datetime
from urllib.parse import quote

import httpx

from dd_log_analyzer.config import AppConfig
from dd_log_analyzer.models.log_entry import Alert, AlertSeverity, AnalysisResult
from dd_log_analyzer.notifications.alert_state import AlertStateDB

logger = logging.getLogger(__name__)


def _build_datadog_link(query: str, site: str = "datadoghq.com") -> str:
    """Build a Datadog Log Explorer deep-link."""
    return f"https://app.{site}/logs?query={quote(query)}"


class JiraNotifier:
    """Creates Jira tickets when anomalies are detected, with dedup support."""

    def __init__(self, config: AppConfig, alert_state: AlertStateDB | None = None):
        self._config = config
        self._jira = config.jira
        self._alert_state = alert_state

        # Build auth header
        if self._jira.email and self._jira.api_token:
            creds = b64encode(f"{self._jira.email}:{self._jira.api_token}".encode()).decode()
            self._auth_header = f"Basic {creds}"
        else:
            self._auth_header = ""

    def _map_priority(self, severity: AlertSeverity) -> str:
        """Map alert severity to Jira priority name."""
        return self._jira.severity_mapping.get(severity.value, "Medium")

    def _build_description(self, alert: Alert) -> str:
        """Build a rich Jira ticket description with Datadog link and details."""
        lines = [
            f"h2. {alert.summary}",
            "",
            f"*Alert Type:* {alert.alert_type.value}",
            f"*Severity:* {alert.severity.value.upper()}",
            f"*Service:* {alert.service or 'N/A'}",
            f"*Detected at:* {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "h3. Description",
            alert.description,
            "",
        ]

        if alert.datadog_link:
            lines.extend([
                "h3. Datadog Log Explorer",
                f"[View Logs in Datadog|{alert.datadog_link}]",
                "",
            ])

        if alert.details:
            lines.append("h3. Details")
            for key, value in alert.details.items():
                lines.append(f"* *{key}:* {value}")
            lines.append("")

        lines.extend([
            "----",
            f"_Created automatically by dd-log-analyzer | Fingerprint: {{{{monospace:{alert.fingerprint[:8]}}}}}_",
        ])

        return "\n".join(lines)

    def create_ticket(
        self,
        alert: Alert,
    ) -> str | None:
        """Create a Jira ticket for an alert.

        Checks dedup first — skips if a ticket already exists for this fingerprint
        within the cooldown window.

        Args:
            alert: The alert to create a ticket for.

        Returns:
            Jira ticket key (e.g. "OPS-1234") or None if skipped/failed.
        """
        if not self._jira.enabled or not self._jira.auto_create:
            logger.debug("Jira auto-creation disabled, skipping")
            return None

        if not self._jira.base_url or not self._auth_header:
            logger.warning("Jira not configured (missing base_url, email, or api_token)")
            return None

        # Check for existing ticket via dedup
        if self._alert_state:
            existing = self._alert_state.get_existing_ticket(alert.fingerprint)
            if existing:
                logger.info(f"Jira ticket already exists for this alert: {existing}")
                return existing

            if not self._alert_state.should_alert(alert.fingerprint, self._config.alerts.cooldown_minutes):
                logger.info(f"Skipping Jira ticket (dedup cooldown): {alert.summary}")
                return None

        # Build Datadog link
        if not alert.datadog_link and alert.datadog_query:
            alert.datadog_link = _build_datadog_link(alert.datadog_query, self._config.datadog.site)

        # Build ticket data
        priority = self._map_priority(alert.severity)
        summary = f"[DD-Alert] {alert.summary}"
        if len(summary) > 255:
            summary = summary[:252] + "..."

        description = self._build_description(alert)

        labels = ["dd-log-analyzer", "auto-created"]
        if alert.service:
            labels.append(alert.service)

        fields: dict = {
            "project": {"key": self._jira.project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": self._jira.issue_type},
            "priority": {"name": priority},
            "labels": labels,
        }

        # Optional per-service assignee
        if alert.service and alert.service in self._jira.assignees:
            assignee = self._jira.assignees[alert.service]
            fields["assignee"] = {"name": assignee}

        payload = {"fields": fields}

        # Create ticket via Jira REST API
        url = f"{self._jira.base_url.rstrip('/')}/rest/api/2/issue"
        try:
            response = httpx.post(
                url,
                json=payload,
                headers={
                    "Authorization": self._auth_header,
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            response.raise_for_status()
            data = response.json()
            ticket_key = data.get("key", "UNKNOWN")

            # Record in alert state
            if self._alert_state:
                self._alert_state.record_alert(alert.fingerprint, jira_key=ticket_key)

            logger.info(f"Jira ticket created: {ticket_key} — {alert.summary}")
            return ticket_key

        except httpx.HTTPError as e:
            logger.error(f"Failed to create Jira ticket: {e}")
            if hasattr(e, "response") and e.response is not None:
                logger.error(f"Jira response: {e.response.text[:500]}")
            return None

    def create_tickets_from_analysis(
        self,
        result: AnalysisResult,
    ) -> dict[str, str]:
        """Create Jira tickets for all anomalies in an analysis result.

        Args:
            result: Analysis result containing anomalies.

        Returns:
            Dict mapping alert fingerprint -> Jira ticket key.
        """
        if not result.anomalies:
            return {}

        tickets: dict[str, str] = {}

        for anomaly in result.anomalies:
            fingerprint = hashlib.sha256(
                f"{anomaly.anomaly_type.value}|{anomaly.service or ''}|{anomaly.description}".encode()
            ).hexdigest()[:16]

            alert = Alert(
                alert_type=anomaly.anomaly_type,
                severity=anomaly.severity,
                service=anomaly.service,
                summary=anomaly.description[:120],
                description=anomaly.description,
                fingerprint=fingerprint,
                datadog_query=result.query,
            )

            ticket_key = self.create_ticket(alert)
            if ticket_key:
                tickets[fingerprint] = ticket_key

        return tickets
