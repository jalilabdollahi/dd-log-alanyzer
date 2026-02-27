"""TTL-based in-memory response cache for Datadog API calls."""

from __future__ import annotations

import hashlib
import time
from typing import Any


class ResponseCache:
    """Simple TTL cache to reduce redundant API calls during iterative analysis."""

    def __init__(self, ttl: int = 300):
        self._ttl = ttl
        self._store: dict[str, tuple[float, Any]] = {}

    @staticmethod
    def _make_key(*args: Any, **kwargs: Any) -> str:
        """Create a deterministic cache key from arguments."""
        raw = f"{args}|{sorted(kwargs.items())}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        """Return cached value if key exists and hasn't expired."""
        if key in self._store:
            ts, value = self._store[key]
            if time.time() - ts < self._ttl:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        """Store a value with current timestamp."""
        self._store[key] = (time.time(), value)

    def invalidate(self, key: str) -> None:
        """Remove a specific key."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Clear entire cache."""
        self._store.clear()

    def cleanup(self) -> int:
        """Remove expired entries. Returns number of entries removed."""
        now = time.time()
        expired = [k for k, (ts, _) in self._store.items() if now - ts >= self._ttl]
        for k in expired:
            del self._store[k]
        return len(expired)
