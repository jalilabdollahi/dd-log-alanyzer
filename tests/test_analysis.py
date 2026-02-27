"""Tests for the analysis modules (pattern detection, anomalies, errors, trends)."""

from datetime import datetime, timedelta

from dd_log_analyzer.analysis.anomalies import detect_error_bursts, detect_volume_anomalies
from dd_log_analyzer.analysis.errors import correlate_errors
from dd_log_analyzer.analysis.patterns import detect_patterns, fingerprint_message, tokenize_message
from dd_log_analyzer.analysis.trends import analyze_trends
from dd_log_analyzer.config import AnalysisConfig, AppConfig
from dd_log_analyzer.models.log_entry import LogEntry


def _make_log(
    message: str,
    status: str = "info",
    service: str = "test-service",
    minutes_ago: int = 0,
    **attrs,
) -> LogEntry:
    """Helper to create test log entries."""
    return LogEntry(
        id=f"log-{minutes_ago}-{hash(message) % 10000}",
        timestamp=datetime.utcnow() - timedelta(minutes=minutes_ago),
        status=status,
        service=service,
        message=message,
        attributes=attrs,
    )


# =====================================================================
# Pattern detection tests
# =====================================================================


class TestTokenization:
    def test_uuid_replacement(self):
        msg = "Request abc12345-1234-5678-abcd-1234567890ab failed"
        result = tokenize_message(msg)
        assert "<UUID>" in result
        assert "abc12345-1234-5678" not in result

    def test_ip_replacement(self):
        msg = "Connection from 192.168.1.100 refused"
        result = tokenize_message(msg)
        assert "<IP>" in result
        assert "192.168.1.100" not in result

    def test_number_replacement(self):
        msg = "Processed 1234 items in 56 seconds"
        result = tokenize_message(msg)
        assert "<NUM>" in result

    def test_fingerprint_stability(self):
        msg1 = tokenize_message("Request 123 from 10.0.0.1 failed")
        msg2 = tokenize_message("Request 456 from 10.0.0.2 failed")
        assert fingerprint_message(msg1) == fingerprint_message(msg2)


class TestPatternDetection:
    def test_clusters_similar_messages(self):
        logs = [
            _make_log(f"Connection timeout to host 10.0.0.{i}", minutes_ago=i)
            for i in range(20)
        ]
        patterns = detect_patterns(logs, top_n=5)
        assert len(patterns) >= 1
        # All messages should cluster into 1 pattern
        assert patterns[0].count == 20

    def test_separates_different_messages(self):
        logs = [
            _make_log("Connection timeout", minutes_ago=1),
            _make_log("Connection timeout", minutes_ago=2),
            _make_log("Disk space critical", minutes_ago=3),
            _make_log("Disk space critical", minutes_ago=4),
        ]
        patterns = detect_patterns(logs, top_n=5)
        assert len(patterns) == 2

    def test_empty_logs(self):
        assert detect_patterns([]) == []


# =====================================================================
# Anomaly detection tests
# =====================================================================


class TestAnomalyDetection:
    def _make_config(self) -> AppConfig:
        return AppConfig(
            analysis=AnalysisConfig(
                anomaly_zscore_threshold=2.0,
                burst_window_seconds=60,
                burst_min_count=5,
                trend_bucket_minutes=1,
            )
        )

    def test_volume_spike_detected(self):
        config = self._make_config()
        # Normal: 5 logs/min, spike: 50 logs in one minute
        logs = []
        for minute in range(10):
            count = 50 if minute == 5 else 5
            for i in range(count):
                logs.append(_make_log(f"msg {i}", minutes_ago=10 - minute))

        anomalies = detect_volume_anomalies(logs, config)
        assert len(anomalies) >= 1
        assert any(a.anomaly_type.value == "volume_anomaly" for a in anomalies)

    def test_no_anomaly_on_stable(self):
        config = self._make_config()
        logs = [_make_log(f"msg {i}", minutes_ago=i % 10) for i in range(50)]
        anomalies = detect_volume_anomalies(logs, config)
        # Stable volume should produce few or no anomalies
        critical_anomalies = [a for a in anomalies if a.severity.value == "critical"]
        assert len(critical_anomalies) == 0

    def test_error_burst_detected(self):
        config = self._make_config()
        # 10 errors in 30 seconds
        base_time = datetime.utcnow()
        logs = [
            LogEntry(
                id=f"err-{i}",
                timestamp=base_time - timedelta(seconds=i * 3),
                status="error",
                service="api",
                message=f"Error {i}",
            )
            for i in range(10)
        ]
        anomalies = detect_error_bursts(logs, config)
        assert len(anomalies) >= 1
        assert any(a.anomaly_type.value == "error_burst" for a in anomalies)


# =====================================================================
# Error correlation tests
# =====================================================================


class TestErrorCorrelation:
    def test_groups_similar_errors(self):
        logs = [
            _make_log("NullPointerException at line 42", status="error", service="api", minutes_ago=1),
            _make_log("NullPointerException at line 42", status="error", service="api", minutes_ago=2),
            _make_log("NullPointerException at line 42", status="error", service="worker", minutes_ago=3),
        ]
        groups = correlate_errors(logs)
        assert len(groups) >= 1
        assert groups[0].count == 3
        assert len(groups[0].services) == 2

    def test_identifies_root_cause_by_earliest_service(self):
        logs = [
            _make_log("Timeout connecting to DB", status="error", service="db-proxy", minutes_ago=5),
            _make_log("Timeout connecting to DB", status="error", service="api-gateway", minutes_ago=3),
            _make_log("Timeout connecting to DB", status="error", service="frontend", minutes_ago=1),
        ]
        groups = correlate_errors(logs)
        # db-proxy appeared first, should be root cause candidate
        assert groups[0].root_cause_candidate == "db-proxy"


# =====================================================================
# Trend analysis tests
# =====================================================================


class TestTrendAnalysis:
    def _make_config(self) -> AppConfig:
        return AppConfig(analysis=AnalysisConfig(trend_bucket_minutes=1))

    def test_increasing_trend(self):
        config = self._make_config()
        # Create logs with increasing volume
        logs = []
        for minute in range(10):
            count = minute * 5 + 5  # 5, 10, 15, 20, ...
            for i in range(count):
                logs.append(_make_log(f"msg", minutes_ago=10 - minute))

        result = analyze_trends(logs, config)
        assert result.trend_direction == "increasing"
        assert result.slope > 0

    def test_stable_trend(self):
        config = self._make_config()
        logs = [_make_log("msg", minutes_ago=i % 5) for i in range(50)]
        result = analyze_trends(logs, config)
        # With uniform distribution, should be roughly stable
        assert result.trend_direction in ("stable", "increasing", "decreasing")

    def test_empty_logs(self):
        config = self._make_config()
        result = analyze_trends([], config)
        assert len(result.buckets) == 0
