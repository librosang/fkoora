"""In-process per-client rate limiting for the JSON API.

Fixed-window counters keyed by client IP, thread-safe, bounded memory.
Single-process only (the API runs as one waitress/gunicorn-async worker with
threads); with multiple OS processes each would enforce its own counters,
which still throttles but multiplies the effective limit.

Tunables (requests per window):
    RATE_LIMIT_GENERAL   - every /api/* endpoint   (default 120/min)
    RATE_LIMIT_IMG       - /api/img                (default 1200/min)
    RATE_LIMIT_CRON      - /api/cron/refresh       (default 30/min)

Set to 0 to disable a limit.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple


class RateLimiter:
    def __init__(self, name: str, limit: int, window_sec: float = 60.0,
                 max_keys: int = 10_000):
        self.name = name
        self.limit = max(0, int(limit))
        self.window_sec = window_sec
        self.max_keys = max_keys
        self._hits: Dict[str, Tuple[int, float]] = {}   # key -> (count, window_start)
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        """Consume one slot for `key`; True if under the limit."""
        if not self.limit:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            count, start = self._hits.get(key, (0, now))
            if now - start >= self.window_sec:          # new window
                count, start = 0, now
            count += 1
            self._hits[key] = (count, start)
            if len(self._hits) > self.max_keys:         # bound memory
                self._prune(now)
            return count <= self.limit

    def retry_after(self, key: str) -> int:
        """Seconds until `key`'s window resets (for Retry-After headers)."""
        with self._lock:
            count, start = self._hits.get(key, (0, 0))
            remaining = start + self.window_sec - time.monotonic()
            return max(1, int(remaining)) if count >= self.limit else 1

    def _prune(self, now: float) -> None:
        stale = [k for k, (_c, s) in self._hits.items() if now - s >= self.window_sec]
        for k in stale:
            self._hits.pop(k, None)
        if len(self._hits) > self.max_keys:             # still too big: drop oldest half
            keep = sorted(self._hits.items(), key=lambda kv: kv[1][1])[: self.max_keys // 2]
            self._hits = dict(keep)
