"""In-memory sliding-window rate limiting (Phase 10 auth hardening).

Discourages login brute-force without persisting anything sensitive. The
window is tracked per key (client IP + account email) in process memory; a
process restart resets it, which is acceptable for a local-first tool.

The limiter never reveals *why* a key is blocked beyond a generic ``429``, so
an attacker cannot probe individual accounts (enumeration-resistant).
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, Tuple


class SlidingWindowLimiter:
    """Sliding-window limiter keyed by string key.

    Allows up to ``max_events`` attempts within ``window_seconds``. A single
    failed attempt consumes a slot; ``allow()`` returns the remaining budget.
    """

    def __init__(self, max_events: int = 10, window_seconds: float = 900.0) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self.max_events = int(max_events)
        self.window_seconds = float(window_seconds)
        self._events: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> None:
        events = self._events.get(key)
        if events is None:
            return
        cutoff = now - self.window_seconds
        while events and events[0] < cutoff:
            events.popleft()
        if not events:
            self._events.pop(key, None)

    def allow(self, key: str) -> bool:
        """Return True if ``key`` may take another attempt now."""
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            events = self._events.setdefault(key, deque())
            if len(events) >= self.max_events:
                return False
            # Record the attempt up front (even the allowed one) so a burst of
            # rapid-firing requests cannot squeeze past the window.
            events.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


# Friendly default: 10 attempts per 15 minutes per key.
class LoginLimiter(SlidingWindowLimiter):
    def __init__(self) -> None:
        super().__init__(max_events=10, window_seconds=900.0)