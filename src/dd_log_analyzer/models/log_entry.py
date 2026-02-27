"""Pydantic data models for log entries, analysis results, and alerts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class LogStatus(str, Enum):
    """Datadog log severity levels."""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    CRITICAL = "critical"


class AlertSeverity(str, Enum):
    """Alert severity levels for notifications."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Types of alerts the analysis engine can produce."""

    VOLUME_ANOMALY = "volume_anomaly"
    ERROR_BURST = "error_burst"
    NEW_ERROR = "new_error"
    FREQUENCY_SHIFT = "frequency_shift"
    THRESHOLD_EXCEEDED = "threshold_exceeded"
    TREND_CHANGE = "trend_change"


# ---------------------------------------------------------------------------
# Core log entry
# ---------------------------------------------------------------------------


class LogEntry(BaseModel):
    """A single parsed log entry from Datadog."""

    id: str
    timestamp: datetime
    status: str
    service: str
    host: str | None = None
    message: str
    attributes: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


class PatternResult(BaseModel):
    """A detected log message pattern (cluster of similar messages)."""

    pattern_id: str
    template: str
    count: int
    percentage: float = 0.0
    services: dict[str, int] = Field(default_factory=dict)
    sample_messages: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


class AnomalyResult(BaseModel):
    """A detected anomaly in log volume, error rates, or patterns."""

    anomaly_type: AlertType
    severity: AlertSeverity
    service: str | None = None
    description: str
    metric_value: float = 0.0
    expected_value: float = 0.0
    zscore: float = 0.0
    window_start: datetime | None = None
    window_end: datetime | None = None
    details: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Error correlation
# ---------------------------------------------------------------------------


class ErrorGroup(BaseModel):
    """A group of correlated errors across services."""

    group_id: str
    error_kind: str | None = None
    fingerprint: str
    count: int
    services: list[str] = Field(default_factory=list)
    trace_ids: list[str] = Field(default_factory=list)
    root_cause_candidate: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    mtbf_seconds: float | None = None
    sample_messages: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Trend analysis
# ---------------------------------------------------------------------------


class TrendBucket(BaseModel):
    """A single time bucket in a trend analysis."""

    timestamp: datetime
    count: int = 0
    error_count: int = 0
    error_rate: float = 0.0


class TrendResult(BaseModel):
    """Time-series trend analysis results."""

    buckets: list[TrendBucket] = Field(default_factory=list)
    slope: float = 0.0
    trend_direction: str = "stable"  # "increasing", "decreasing", "stable"
    baseline_avg: float = 0.0
    current_avg: float = 0.0
    change_percentage: float = 0.0


# ---------------------------------------------------------------------------
# Aggregated analysis result
# ---------------------------------------------------------------------------


class AggregationBucket(BaseModel):
    """A single bucket from Datadog's aggregate_logs API."""

    group_by: dict[str, str] = Field(default_factory=dict)
    count: int = 0
    computed: dict[str, float] = Field(default_factory=dict)


class AggregationResult(BaseModel):
    """Result from Datadog's aggregate_logs endpoint."""

    buckets: list[AggregationBucket] = Field(default_factory=list)
    total: int = 0


class AnalysisResult(BaseModel):
    """Complete analysis result aggregating all modules."""

    query: str
    time_from: datetime
    time_to: datetime
    total_logs: int = 0
    patterns: list[PatternResult] = Field(default_factory=list)
    anomalies: list[AnomalyResult] = Field(default_factory=list)
    error_groups: list[ErrorGroup] = Field(default_factory=list)
    trends: TrendResult = Field(default_factory=TrendResult)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Alert models
# ---------------------------------------------------------------------------


class Alert(BaseModel):
    """An alert ready to be sent to Slack / Jira."""

    alert_type: AlertType
    severity: AlertSeverity
    service: str | None = None
    summary: str
    description: str
    details: dict = Field(default_factory=dict)
    fingerprint: str = ""
    datadog_query: str = ""
    datadog_link: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
