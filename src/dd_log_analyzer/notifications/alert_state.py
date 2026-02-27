"""SQLite-based alert deduplication — prevents Slack flooding and duplicate Jira tickets."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


_DEFAULT_DB_DIR = Path.home() / ".dd-log-analyzer"
_DEFAULT_DB_PATH = _DEFAULT_DB_DIR / "alert_state.db"


class AlertStateDB:
    """Stores alert fingerprints with timestamps and optional Jira ticket keys.

    Auto-creates the database and table on first use.
    """

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._init_db()

    def _init_db(self) -> None:
        """Create the alerts table if it doesn't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_state (
                fingerprint TEXT PRIMARY KEY,
                last_fired REAL NOT NULL,
                jira_key TEXT,
                count INTEGER DEFAULT 1
            )
        """)
        self._conn.commit()

    def should_alert(self, fingerprint: str, cooldown_minutes: int = 15) -> bool:
        """Check if an alert with this fingerprint should fire.

        Returns True if:
        - This fingerprint has never been seen, OR
        - The cooldown period has elapsed since the last firing.
        """
        row = self._conn.execute(
            "SELECT last_fired FROM alert_state WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()

        if row is None:
            return True

        elapsed = time.time() - row[0]
        return elapsed >= (cooldown_minutes * 60)

    def record_alert(self, fingerprint: str, jira_key: str | None = None) -> None:
        """Record that an alert was fired for this fingerprint."""
        self._conn.execute(
            """
            INSERT INTO alert_state (fingerprint, last_fired, jira_key, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(fingerprint) DO UPDATE SET
                last_fired = excluded.last_fired,
                jira_key = COALESCE(excluded.jira_key, alert_state.jira_key),
                count = alert_state.count + 1
            """,
            (fingerprint, time.time(), jira_key),
        )
        self._conn.commit()

    def get_existing_ticket(self, fingerprint: str) -> str | None:
        """Get the Jira ticket key for a previously alerted fingerprint, if any."""
        row = self._conn.execute(
            "SELECT jira_key FROM alert_state WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        return row[0] if row and row[0] else None

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Remove entries older than max_age_hours. Returns count removed."""
        cutoff = time.time() - (max_age_hours * 3600)
        cursor = self._conn.execute(
            "DELETE FROM alert_state WHERE last_fired < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
