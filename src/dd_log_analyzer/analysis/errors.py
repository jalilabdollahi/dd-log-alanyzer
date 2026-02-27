"""Error correlator — group, correlate, find root causes across services."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime

from dd_log_analyzer.analysis.patterns import tokenize_message
from dd_log_analyzer.models.log_entry import ErrorGroup, LogEntry


def _safe_nested_get(attrs: dict, key1: str, key2: str, default: str = "") -> str:
    """Safely get a nested dict value, handling cases where the value is a string."""
    if not isinstance(attrs, dict):
        return default
    val = attrs.get(key1)
    if isinstance(val, dict):
        return val.get(key2, default)
    return default


def _error_fingerprint(log: LogEntry) -> str:
    """Create a fingerprint for error grouping."""
    # Use error kind + tokenized message for grouping
    error_kind = _safe_nested_get(log.attributes, "error", "kind")
    tokenized = tokenize_message(log.message)
    raw = f"{error_kind}|{tokenized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def correlate_errors(
    logs: list[LogEntry],
    top_n: int = 20,
) -> list[ErrorGroup]:
    """Group and correlate errors across services.

    Groups errors by fingerprint (error kind + tokenized message pattern),
    tracks service distribution, collects trace IDs for cross-service correlation,
    and identifies root-cause candidates.

    Args:
        logs: Log entries (typically pre-filtered to error/critical status).
        top_n: Max number of error groups to return.

    Returns:
        List of ErrorGroup ordered by count (most frequent first).
    """
    error_logs = [l for l in logs if l.status in ("error", "critical")]
    if not error_logs:
        return []

    groups: dict[str, dict] = {}

    for log in error_logs:
        fp = _error_fingerprint(log)

        if fp not in groups:
            error_kind = _safe_nested_get(log.attributes, "error", "kind")
            groups[fp] = {
                "error_kind": error_kind,
                "count": 0,
                "services": set(),
                "trace_ids": set(),
                "samples": [],
                "first_seen": log.timestamp,
                "last_seen": log.timestamp,
                "timestamps": [],
            }

        group = groups[fp]
        group["count"] += 1
        group["services"].add(log.service)
        group["timestamps"].append(log.timestamp)

        # Extract trace ID if present
        trace_id = _safe_nested_get(log.attributes, "dd", "trace_id") or (
            log.attributes.get("trace_id") if isinstance(log.attributes, dict) else None
        )
        if trace_id:
            group["trace_ids"].add(str(trace_id))

        if len(group["samples"]) < 3:
            group["samples"].append(log.message)

        if log.timestamp < group["first_seen"]:
            group["first_seen"] = log.timestamp
        if log.timestamp > group["last_seen"]:
            group["last_seen"] = log.timestamp

    # Calculate MTBF for each group
    for fp, group in groups.items():
        timestamps = sorted(group["timestamps"])
        if len(timestamps) >= 2:
            total_span = (timestamps[-1] - timestamps[0]).total_seconds()
            group["mtbf"] = total_span / (len(timestamps) - 1) if len(timestamps) > 1 else None
        else:
            group["mtbf"] = None

    # Find root cause candidates: for groups with trace_ids, find the earliest error
    # in the trace chain across all services
    sorted_groups = sorted(groups.items(), key=lambda x: x[1]["count"], reverse=True)[:top_n]

    results: list[ErrorGroup] = []
    for fp, data in sorted_groups:
        # Simple root cause heuristic: the service with the earliest occurrence
        root_cause = None
        if len(data["services"]) > 1:
            service_first_seen: dict[str, datetime] = {}
            for log in error_logs:
                log_fp = _error_fingerprint(log)
                if log_fp == fp:
                    if log.service not in service_first_seen or log.timestamp < service_first_seen[log.service]:
                        service_first_seen[log.service] = log.timestamp
            if service_first_seen:
                root_cause = min(service_first_seen, key=service_first_seen.get)

        results.append(
            ErrorGroup(
                group_id=fp,
                error_kind=data["error_kind"] or None,
                fingerprint=fp,
                count=data["count"],
                services=sorted(data["services"]),
                trace_ids=list(data["trace_ids"])[:10],
                root_cause_candidate=root_cause,
                first_seen=data["first_seen"],
                last_seen=data["last_seen"],
                mtbf_seconds=data["mtbf"],
                sample_messages=data["samples"],
            )
        )

    return results
