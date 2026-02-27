"""Query builder — structured filters and raw Datadog query pass-through."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from dd_log_analyzer.client import DatadogLogClient
from dd_log_analyzer.config import AppConfig
from dd_log_analyzer.models.log_entry import AggregationResult, LogEntry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Time range parser
# ---------------------------------------------------------------------------

_TIME_PATTERN = re.compile(r"^last\s+(\d+)\s*(m|min|h|hr|d|day)s?$", re.IGNORECASE)


def parse_time_range(time_str: str) -> tuple[datetime, datetime]:
    """Parse human-friendly time ranges into (from, to) datetimes.

    Supports:
        "last 15m", "last 1h", "last 24h", "last 7d",
        "today", or ISO format "2026-02-11T00:00:00Z/2026-02-11T23:59:59Z"
    """
    now = datetime.utcnow()
    time_str = time_str.strip()

    if time_str.lower() == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0), now

    match = _TIME_PATTERN.match(time_str)
    if match:
        value = int(match.group(1))
        unit = match.group(2).lower()
        if unit in ("m", "min"):
            delta = timedelta(minutes=value)
        elif unit in ("h", "hr"):
            delta = timedelta(hours=value)
        elif unit in ("d", "day"):
            delta = timedelta(days=value)
        else:
            delta = timedelta(hours=1)
        return now - delta, now

    # Try ISO range split by /
    if "/" in time_str:
        parts = time_str.split("/", 1)
        return datetime.fromisoformat(parts[0].replace("Z", "+00:00")), datetime.fromisoformat(
            parts[1].replace("Z", "+00:00")
        )

    raise ValueError(f"Cannot parse time range: '{time_str}'. Use 'last 1h', 'last 30m', 'today', or ISO/ISO format.")


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------


def build_query(
    raw: str | None = None,
    env: str | None = None,
    services: list[str] | None = None,
    status: list[str] | None = None,
    exclude_status: list[str] | None = None,
    hosts: list[str] | None = None,
    tags: list[str] | None = None,
    message_contains: str | None = None,
    scope_env: str | None = None,
) -> str:
    """Build a Datadog query string from structured filters.

    If `raw` is provided, it is used as-is with the global scope prepended.
    Otherwise, builds the query from individual filter parameters.
    """
    parts: list[str] = []

    # Global scope
    if scope_env:
        parts.append(f"env:{scope_env}")

    if raw:
        parts.append(raw)
        return " ".join(parts)

    if env and env != scope_env:
        parts.append(f"env:{env}")

    if services:
        if len(services) == 1:
            parts.append(f"service:{services[0]}")
        else:
            svc_list = " OR ".join(services)
            parts.append(f"service:({svc_list})")

    if status:
        if len(status) == 1:
            parts.append(f"status:{status[0]}")
        else:
            st_list = " OR ".join(status)
            parts.append(f"status:({st_list})")

    if exclude_status:
        ex_list = " OR ".join(exclude_status)
        parts.append(f"-status:({ex_list})")

    if hosts:
        if len(hosts) == 1:
            parts.append(f"host:{hosts[0]}")
        else:
            h_list = " OR ".join(hosts)
            parts.append(f"host:({h_list})")

    if tags:
        for tag in tags:
            parts.append(tag)

    if message_contains:
        parts.append(f'@msg:"{message_contains}"')

    return " ".join(parts) if parts else "*"


# ---------------------------------------------------------------------------
# Query Engine
# ---------------------------------------------------------------------------


class QueryEngine:
    """Builds and executes Datadog log queries with preset and scope support."""

    def __init__(self, client: DatadogLogClient, config: AppConfig):
        self._client = client
        self._config = config

    def _apply_scope(self, query: str) -> str:
        """Prepend global scope to query if not already present."""
        scope = self._config.scope
        parts: list[str] = []

        if scope.env and f"env:{scope.env}" not in query:
            parts.append(f"env:{scope.env}")

        if scope.services:
            for svc in scope.services:
                if f"service:{svc}" not in query:
                    parts.append(f"service:{svc}")

        if parts:
            return " ".join(parts) + " " + query
        return query

    def resolve_preset(self, preset_name: str) -> str:
        """Resolve a named preset into a query string."""
        if preset_name not in self._config.presets:
            available = ", ".join(self._config.presets.keys()) if self._config.presets else "(none)"
            raise ValueError(f"Unknown preset '{preset_name}'. Available: {available}")

        preset = self._config.presets[preset_name]
        parts: list[str] = []

        if preset.services:
            if len(preset.services) == 1:
                parts.append(f"service:{preset.services[0]}")
            else:
                svc_list = " OR ".join(preset.services)
                parts.append(f"service:({svc_list})")

        parts.append(preset.query)
        return " ".join(parts)

    def query(
        self,
        raw: str | None = None,
        preset: str | None = None,
        env: str | None = None,
        services: list[str] | None = None,
        status: list[str] | None = None,
        exclude_status: list[str] | None = None,
        message_contains: str | None = None,
        time_range: str = "last 1h",
        limit: int | None = None,
    ) -> list[LogEntry]:
        """Execute a log search query.

        Args:
            raw: Raw Datadog query string (passed as-is).
            preset: Named preset from config.
            env: Environment filter.
            services: Service filters.
            status: Include only these statuses.
            exclude_status: Exclude these statuses.
            message_contains: Message text filter.
            time_range: Time range string (e.g. "last 1h").
            limit: Max results (default from config).

        Returns:
            List of matching LogEntry objects.
        """
        if limit is None:
            limit = self._config.datadog.max_results

        # Build the query
        if preset:
            query_str = self.resolve_preset(preset)
        elif raw:
            query_str = raw
        else:
            query_str = build_query(
                env=env,
                services=services,
                status=status,
                exclude_status=exclude_status,
                message_contains=message_contains,
            )

        # Apply global scope
        query_str = self._apply_scope(query_str)

        # Parse time range
        time_from, time_to = parse_time_range(time_range)

        logger.info(f"Executing query: {query_str} | range: {time_from} → {time_to} | limit: {limit}")

        return self._client.search_logs(
            query=query_str,
            time_from=time_from,
            time_to=time_to,
            limit=limit,
        )

    def aggregate(
        self,
        raw: str | None = None,
        preset: str | None = None,
        time_range: str = "last 1h",
        group_by: list[str] | None = None,
    ) -> AggregationResult:
        """Execute a server-side log aggregation (Tier 1 — scans ALL logs).

        Args:
            raw: Raw Datadog query string.
            preset: Named preset from config.
            time_range: Time range string.
            group_by: Facets to group by (e.g. ["service", "status"]).

        Returns:
            AggregationResult with bucketed counts.
        """
        if preset:
            query_str = self.resolve_preset(preset)
        elif raw:
            query_str = raw
        else:
            query_str = "*"

        query_str = self._apply_scope(query_str)
        time_from, time_to = parse_time_range(time_range)

        logger.info(f"Aggregating: {query_str} | range: {time_from} → {time_to} | group_by: {group_by}")

        return self._client.aggregate_logs(
            query=query_str,
            time_from=time_from,
            time_to=time_to,
            group_by=group_by,
        )
