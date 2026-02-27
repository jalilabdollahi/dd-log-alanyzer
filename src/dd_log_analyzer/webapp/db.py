"""SQLite database for anomaly history persistence."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

_default_db = Path(__file__).resolve().parent.parent.parent.parent / "data" / "anomaly_history.db"
DB_PATH = Path(os.getenv("DB_PATH", str(_default_db)))


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            service TEXT,
            anomaly_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            description TEXT,
            metric_value REAL DEFAULT 0,
            expected_value REAL DEFAULT 0,
            query TEXT,
            fingerprint TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomalies_ts ON anomalies (timestamp DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_anomalies_svc ON anomalies (service)
    """)
    conn.commit()
    conn.close()


def save_anomaly(
    timestamp: str,
    service: str | None,
    anomaly_type: str,
    severity: str,
    description: str,
    metric_value: float = 0,
    expected_value: float = 0,
    query: str = "",
    fingerprint: str = "",
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO anomalies
           (timestamp, service, anomaly_type, severity, description, metric_value, expected_value, query, fingerprint)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (timestamp, service, anomaly_type, severity, description, metric_value, expected_value, query, fingerprint),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id


def list_anomalies(
    limit: int = 50,
    offset: int = 0,
    service: str | None = None,
    severity: str | None = None,
    since: str | None = None,
) -> list[dict]:
    conn = _get_conn()
    query = "SELECT * FROM anomalies WHERE 1=1"
    params: list = []
    if service:
        query += " AND service = ?"
        params.append(service)
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    if since:
        query += " AND timestamp >= ?"
        params.append(since)
    query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_anomalies(
    service: str | None = None,
    severity: str | None = None,
    since: str | None = None,
) -> int:
    conn = _get_conn()
    query = "SELECT COUNT(*) FROM anomalies WHERE 1=1"
    params: list = []
    if service:
        query += " AND service = ?"
        params.append(service)
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    if since:
        query += " AND timestamp >= ?"
        params.append(since)
    count = conn.execute(query, params).fetchone()[0]
    conn.close()
    return count


def get_services_with_counts(since: str | None = None) -> list[dict]:
    conn = _get_conn()
    params: list = []
    where = ""
    if since:
        where = "WHERE timestamp >= ?"
        params.append(since)
    rows = conn.execute(
        f"SELECT service, severity, COUNT(*) as count FROM anomalies {where} GROUP BY service, severity ORDER BY count DESC",
        params,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
