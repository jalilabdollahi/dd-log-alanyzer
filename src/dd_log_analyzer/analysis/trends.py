"""Trend analyzer — rolling averages, regression, and baseline comparison."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np

from dd_log_analyzer.config import AppConfig
from dd_log_analyzer.models.log_entry import LogEntry, TrendBucket, TrendResult

logger = logging.getLogger(__name__)


def _build_buckets(
    logs: list[LogEntry],
    bucket_minutes: int = 5,
) -> list[TrendBucket]:
    """Build time-series buckets from log entries."""
    if not logs:
        return []

    logs_sorted = sorted(logs, key=lambda l: l.timestamp)
    bucket_delta = timedelta(minutes=bucket_minutes)
    start = logs_sorted[0].timestamp
    end = logs_sorted[-1].timestamp

    buckets: list[TrendBucket] = []
    current_start = start
    idx = 0

    while current_start <= end:
        current_end = current_start + bucket_delta
        count = 0
        error_count = 0

        while idx < len(logs_sorted) and logs_sorted[idx].timestamp < current_end:
            count += 1
            if logs_sorted[idx].status in ("error", "critical"):
                error_count += 1
            idx += 1

        error_rate = (error_count / count * 100) if count > 0 else 0.0
        buckets.append(
            TrendBucket(
                timestamp=current_start,
                count=count,
                error_count=error_count,
                error_rate=round(error_rate, 2),
            )
        )
        current_start = current_end

    return buckets


def analyze_trends(
    logs: list[LogEntry],
    config: AppConfig,
) -> TrendResult:
    """Analyze log volume and error rate trends over time.

    Computes:
    - Time-bucketed counts and error rates
    - Linear regression slope to detect trend direction
    - Baseline vs. current window comparison
    - Change percentage

    Args:
        logs: Log entries to analyze.
        config: App config for bucket size settings.

    Returns:
        TrendResult with buckets, slope, and direction assessment.
    """
    bucket_minutes = config.analysis.trend_bucket_minutes
    buckets = _build_buckets(logs, bucket_minutes)

    if len(buckets) < 2:
        return TrendResult(buckets=buckets)

    counts = np.array([b.count for b in buckets], dtype=float)
    x = np.arange(len(counts), dtype=float)

    # Linear regression
    if len(x) >= 2:
        coeffs = np.polyfit(x, counts, 1)
        slope = float(coeffs[0])
    else:
        slope = 0.0

    # Determine trend direction using slope relative to mean
    mean_count = float(np.mean(counts))
    if mean_count > 0:
        relative_slope = slope / mean_count
    else:
        relative_slope = 0.0

    if relative_slope > 0.05:
        direction = "increasing"
    elif relative_slope < -0.05:
        direction = "decreasing"
    else:
        direction = "stable"

    # Baseline comparison: first half vs second half
    mid = len(buckets) // 2
    baseline_counts = counts[:mid]
    current_counts = counts[mid:]

    baseline_avg = float(np.mean(baseline_counts)) if len(baseline_counts) > 0 else 0.0
    current_avg = float(np.mean(current_counts)) if len(current_counts) > 0 else 0.0

    change_pct = 0.0
    if baseline_avg > 0:
        change_pct = round(((current_avg - baseline_avg) / baseline_avg) * 100, 2)

    return TrendResult(
        buckets=buckets,
        slope=round(slope, 4),
        trend_direction=direction,
        baseline_avg=round(baseline_avg, 2),
        current_avg=round(current_avg, 2),
        change_percentage=change_pct,
    )
