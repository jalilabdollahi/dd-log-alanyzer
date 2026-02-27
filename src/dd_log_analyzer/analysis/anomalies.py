"""Anomaly detector — volume spikes, error bursts, and frequency shifts."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

from dd_log_analyzer.config import AppConfig
from dd_log_analyzer.models.log_entry import (
    AlertSeverity,
    AlertType,
    AggregationBucket,
    AnomalyResult,
    LogEntry,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Volume anomaly detection via Z-score
# ---------------------------------------------------------------------------


def _bucket_logs_by_time(
    logs: list[LogEntry],
    bucket_minutes: int = 5,
) -> list[tuple[datetime, int]]:
    """Group logs into time buckets and count per bucket."""
    if not logs:
        return []

    logs_sorted = sorted(logs, key=lambda l: l.timestamp)
    bucket_delta = timedelta(minutes=bucket_minutes)
    start = logs_sorted[0].timestamp
    end = logs_sorted[-1].timestamp

    buckets: list[tuple[datetime, int]] = []
    current_start = start
    idx = 0

    while current_start <= end:
        current_end = current_start + bucket_delta
        count = 0
        while idx < len(logs_sorted) and logs_sorted[idx].timestamp < current_end:
            count += 1
            idx += 1
        buckets.append((current_start, count))
        current_start = current_end

    return buckets


def detect_volume_anomalies(
    logs: list[LogEntry],
    config: AppConfig,
) -> list[AnomalyResult]:
    """Detect volume spikes/drops using Z-score analysis.

    Args:
        logs: Log entries to analyze.
        config: App config for thresholds.

    Returns:
        List of detected volume anomalies.
    """
    bucket_minutes = config.analysis.trend_bucket_minutes
    threshold = config.analysis.anomaly_zscore_threshold
    buckets = _bucket_logs_by_time(logs, bucket_minutes)

    if len(buckets) < 3:
        return []

    counts = np.array([c for _, c in buckets])
    mean = float(np.mean(counts))
    std = float(np.std(counts))

    if std == 0:
        return []

    anomalies: list[AnomalyResult] = []

    for ts, count in buckets:
        zscore = (count - mean) / std
        if abs(zscore) >= threshold:
            severity = AlertSeverity.CRITICAL if abs(zscore) >= threshold * 1.5 else AlertSeverity.WARNING
            direction = "spike" if zscore > 0 else "drop"
            anomalies.append(
                AnomalyResult(
                    anomaly_type=AlertType.VOLUME_ANOMALY,
                    severity=severity,
                    description=f"Volume {direction}: {count} logs in {bucket_minutes}min window "
                    f"(expected ~{mean:.0f}, z-score: {zscore:+.2f})",
                    metric_value=float(count),
                    expected_value=mean,
                    zscore=zscore,
                    window_start=ts,
                    window_end=ts + timedelta(minutes=bucket_minutes),
                )
            )

    return anomalies


# ---------------------------------------------------------------------------
# Error burst detection
# ---------------------------------------------------------------------------


def detect_error_bursts(
    logs: list[LogEntry],
    config: AppConfig,
) -> list[AnomalyResult]:
    """Detect concentrated bursts of errors within short time windows.

    Args:
        logs: Log entries (pre-filtered or not).
        config: App config for burst thresholds.

    Returns:
        List of detected error bursts.
    """
    error_logs = [l for l in logs if l.status in ("error", "critical")]
    if not error_logs:
        return []

    window_seconds = config.analysis.burst_window_seconds
    min_count = config.analysis.burst_min_count
    error_logs.sort(key=lambda l: l.timestamp)

    anomalies: list[AnomalyResult] = []
    window_start = 0

    for i, log in enumerate(error_logs):
        # Slide window start forward
        while (log.timestamp - error_logs[window_start].timestamp).total_seconds() > window_seconds:
            window_start += 1

        burst_count = i - window_start + 1
        if burst_count >= min_count:
            # Check we haven't already reported a burst in this window
            burst_start = error_logs[window_start].timestamp
            already_reported = any(
                a.window_start == burst_start for a in anomalies if a.anomaly_type == AlertType.ERROR_BURST
            )
            if not already_reported:
                # Count services affected
                services_in_burst = set()
                for j in range(window_start, i + 1):
                    services_in_burst.add(error_logs[j].service)

                severity = AlertSeverity.CRITICAL if burst_count >= min_count * 3 else AlertSeverity.WARNING
                anomalies.append(
                    AnomalyResult(
                        anomaly_type=AlertType.ERROR_BURST,
                        severity=severity,
                        description=f"Error burst: {burst_count} errors in {window_seconds}s "
                        f"across {len(services_in_burst)} service(s)",
                        metric_value=float(burst_count),
                        expected_value=0.0,
                        window_start=burst_start,
                        window_end=log.timestamp,
                        details={"services": list(services_in_burst)},
                    )
                )

    return anomalies


# ---------------------------------------------------------------------------
# Aggregation-based anomaly detection (Tier 1)
# ---------------------------------------------------------------------------


def detect_anomalies_from_aggregation(
    buckets: list[AggregationBucket],
    config: AppConfig,
    facet_name: str = "service",
) -> list[AnomalyResult]:
    """Detect anomalies from pre-aggregated data (server-side, covers all 2M logs).

    Looks for services with unusually high error counts compared to peers.

    Args:
        buckets: Aggregation buckets from Datadog's aggregate API.
        config: App config.
        facet_name: The facet that was grouped by.

    Returns:
        List of anomalies detected from aggregate data.
    """
    if len(buckets) < 2:
        return []

    counts = np.array([b.count for b in buckets], dtype=float)
    mean = float(np.mean(counts))
    std = float(np.std(counts))
    threshold = config.analysis.anomaly_zscore_threshold

    if std == 0:
        return []

    anomalies: list[AnomalyResult] = []
    for bucket in buckets:
        zscore = (bucket.count - mean) / std
        if zscore >= threshold:
            service_name = bucket.group_by.get(facet_name, "unknown")
            severity = AlertSeverity.CRITICAL if zscore >= threshold * 1.5 else AlertSeverity.WARNING
            anomalies.append(
                AnomalyResult(
                    anomaly_type=AlertType.VOLUME_ANOMALY,
                    severity=severity,
                    service=service_name,
                    description=f"Service '{service_name}' has {bucket.count} logs "
                    f"(avg: {mean:.0f}, z-score: {zscore:+.2f})",
                    metric_value=float(bucket.count),
                    expected_value=mean,
                    zscore=zscore,
                )
            )

    return anomalies
