"""Analysis engine — orchestrates all analysis modules against log data."""

from __future__ import annotations

import logging
from datetime import datetime

from dd_log_analyzer.analysis.anomalies import (
    detect_anomalies_from_aggregation,
    detect_error_bursts,
    detect_volume_anomalies,
)
from dd_log_analyzer.analysis.errors import correlate_errors
from dd_log_analyzer.analysis.patterns import detect_patterns
from dd_log_analyzer.analysis.trends import analyze_trends
from dd_log_analyzer.config import AppConfig
from dd_log_analyzer.models.log_entry import (
    AggregationResult,
    AnalysisResult,
    AnomalyResult,
    LogEntry,
)

logger = logging.getLogger(__name__)


class AnalysisEngine:
    """Orchestrates pattern detection, anomaly detection, error correlation, and trend analysis."""

    def __init__(self, config: AppConfig):
        self._config = config

    def analyze(
        self,
        logs: list[LogEntry],
        query: str,
        time_from: datetime,
        time_to: datetime,
        aggregation: AggregationResult | None = None,
    ) -> AnalysisResult:
        """Run all analysis modules and merge results.

        Uses a two-tier approach:
        - Tier 1 (aggregation): If aggregation data is provided, detect server-side anomalies.
        - Tier 2 (sampled logs): Run pattern detection, error correlation, trend analysis on log samples.

        Args:
            logs: Sampled log entries for detailed analysis (Tier 2).
            query: The original query string.
            time_from: Analysis window start.
            time_to: Analysis window end.
            aggregation: Optional server-side aggregation data (Tier 1).

        Returns:
            AnalysisResult with merged findings from all modules.
        """
        logger.info(f"Starting analysis: {len(logs)} logs, query='{query[:80]}'")

        anomalies: list[AnomalyResult] = []

        # Tier 1 — aggregation-based anomaly detection (covers ALL logs)
        if aggregation and aggregation.buckets:
            logger.info("Tier 1: Detecting anomalies from aggregation data")
            agg_anomalies = detect_anomalies_from_aggregation(
                aggregation.buckets,
                self._config,
            )
            anomalies.extend(agg_anomalies)
            logger.info(f"  → Found {len(agg_anomalies)} anomalies from aggregation")

        # Tier 2 — detailed analysis on sampled logs
        if logs:
            # Volume anomalies
            logger.info("Tier 2: Detecting volume anomalies from sampled logs")
            vol_anomalies = detect_volume_anomalies(logs, self._config)
            anomalies.extend(vol_anomalies)

            # Error bursts
            logger.info("Tier 2: Detecting error bursts")
            burst_anomalies = detect_error_bursts(logs, self._config)
            anomalies.extend(burst_anomalies)

            # Pattern detection
            logger.info("Tier 2: Detecting log patterns")
            patterns = detect_patterns(logs, top_n=self._config.analysis.top_patterns)

            # Error correlation
            logger.info("Tier 2: Correlating errors")
            error_groups = correlate_errors(logs, top_n=self._config.analysis.top_patterns)

            # Trend analysis
            logger.info("Tier 2: Analyzing trends")
            trends = analyze_trends(logs, self._config)
        else:
            patterns = []
            error_groups = []
            from dd_log_analyzer.models.log_entry import TrendResult

            trends = TrendResult()

        result = AnalysisResult(
            query=query,
            time_from=time_from,
            time_to=time_to,
            total_logs=len(logs),
            patterns=patterns,
            anomalies=anomalies,
            error_groups=error_groups,
            trends=trends,
            generated_at=datetime.utcnow(),
        )

        logger.info(
            f"Analysis complete: {len(patterns)} patterns, {len(anomalies)} anomalies, "
            f"{len(error_groups)} error groups, trend: {trends.trend_direction}"
        )

        return result
