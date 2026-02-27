"""Pattern detector — tokenize, fingerprint, and cluster similar log messages."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime

from dd_log_analyzer.models.log_entry import LogEntry, PatternResult

# ---------------------------------------------------------------------------
# Tokenization — replace dynamic values with placeholders
# ---------------------------------------------------------------------------

_REPLACEMENTS = [
    # UUIDs
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE), "<UUID>"),
    # ISO timestamps
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?"), "<TIMESTAMP>"),
    # IP addresses
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<IP>"),
    # Hex hashes (16+ chars)
    (re.compile(r"\b[0-9a-f]{16,}\b", re.IGNORECASE), "<HEX>"),
    # Numbers (standalone)
    (re.compile(r"\b\d+\b"), "<NUM>"),
    # Quoted strings
    (re.compile(r'"[^"]*"'), "<STR>"),
    (re.compile(r"'[^']*'"), "<STR>"),
]


def tokenize_message(message: str) -> str:
    """Replace dynamic values in a log message with placeholders."""
    result = message
    for pattern, replacement in _REPLACEMENTS:
        result = pattern.sub(replacement, result)
    # Collapse repeated placeholders
    result = re.sub(r"(<\w+>)(\s*\1)+", r"\1", result)
    return result.strip()


def fingerprint_message(tokenized: str) -> str:
    """Generate a stable hash fingerprint for a tokenized message."""
    return hashlib.sha256(tokenized.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Pattern detection
# ---------------------------------------------------------------------------


def detect_patterns(
    logs: list[LogEntry],
    top_n: int = 20,
) -> list[PatternResult]:
    """Cluster log messages into patterns using tokenization and fingerprinting.

    Args:
        logs: List of log entries to analyze.
        top_n: Number of top patterns to return.

    Returns:
        List of PatternResult ordered by frequency (most common first).
    """
    if not logs:
        return []

    # Group by fingerprint
    groups: dict[str, dict] = {}

    for log in logs:
        tokenized = tokenize_message(log.message)
        fp = fingerprint_message(tokenized)

        if fp not in groups:
            groups[fp] = {
                "template": tokenized,
                "count": 0,
                "services": Counter(),
                "samples": [],
                "first_seen": log.timestamp,
                "last_seen": log.timestamp,
            }

        group = groups[fp]
        group["count"] += 1
        group["services"][log.service] += 1

        if len(group["samples"]) < 3:
            group["samples"].append(log.message)

        if log.timestamp < group["first_seen"]:
            group["first_seen"] = log.timestamp
        if log.timestamp > group["last_seen"]:
            group["last_seen"] = log.timestamp

    # Sort by count and take top_n
    total = len(logs)
    sorted_groups = sorted(groups.items(), key=lambda x: x[1]["count"], reverse=True)[:top_n]

    return [
        PatternResult(
            pattern_id=fp,
            template=data["template"],
            count=data["count"],
            percentage=round((data["count"] / total) * 100, 2) if total > 0 else 0,
            services=dict(data["services"]),
            sample_messages=data["samples"],
            first_seen=data["first_seen"],
            last_seen=data["last_seen"],
        )
        for fp, data in sorted_groups
    ]
