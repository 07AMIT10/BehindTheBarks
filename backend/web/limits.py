"""One rate limiter for the web server (H4).

Four shapes, one class, one bounded key table:

- `check_interval(key, min_interval_s)`: a minimum gap between two accepted calls per key.
- `hit(key, limit, window_s)`: a sliding window of at most `limit` attempts per `window_s`.
- `record_failure(key)` / `record_success(key)` / `locked_for(key)`: failure lockout. Ten failures
  within ten minutes lock the key for five minutes; each further lockout doubles the length up to one
  hour. A success clears the key's failures and its lockout history.
- Pruning: once the table holds more than `max_keys` keys, entries whose state can no longer matter are
  dropped first, then the entries that expire soonest, so memory stays bounded.

The clock is injected so tests can drive time. Nothing here knows about HTTP.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

MAX_KEYS = 4096
FAIL_LIMIT = 10
FAIL_WINDOW_S = 600.0
BASE_LOCK_S = 300.0
MAX_LOCK_S = 3600.0


@dataclass
class _Entry:
    last_call: float = float("-inf")  # check_interval: the last accepted call
    interval_s: float = 0.0
    hits: list[float] = field(default_factory=list)  # hit(): accepted attempts inside the window
    window_s: float = 0.0
    fails: list[float] = field(default_factory=list)  # failures inside FAIL_WINDOW_S
    lock_until: float = 0.0
    lock_level: int = 0  # lockouts since the last success: the next lock is BASE * 2**level

    def horizon(self) -> float:
        """The instant after which this entry no longer affects any decision."""
        ends = [self.last_call + self.interval_s, self.lock_until]
        if self.hits:
            ends.append(self.hits[-1] + self.window_s)
        if self.fails:
            ends.append(self.fails[-1] + FAIL_WINDOW_S)
        return max(ends)


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.time, *, max_keys: int = MAX_KEYS,
                 fail_limit: int = FAIL_LIMIT, fail_window_s: float = FAIL_WINDOW_S,
                 base_lock_s: float = BASE_LOCK_S, max_lock_s: float = MAX_LOCK_S) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._fail_limit = fail_limit
        self._fail_window_s = fail_window_s
        self._base_lock_s = base_lock_s
        self._max_lock_s = max_lock_s
        self._entries: dict[str, _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def _entry(self, key: str) -> _Entry:
        now = self._clock()
        if key not in self._entries and len(self._entries) >= self._max_keys:
            self._prune(now)
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = _Entry()
        return entry

    def _prune(self, now: float) -> None:
        for k in [k for k, e in self._entries.items() if e.horizon() <= now]:
            del self._entries[k]
        if len(self._entries) >= self._max_keys:
            # still full of live state: drop the entries that expire soonest, keeping room for one key
            by_horizon = sorted(self._entries.items(), key=lambda kv: kv[1].horizon())
            for k, _ in by_horizon[: len(self._entries) - self._max_keys + 1]:
                del self._entries[k]

    # -- minimum interval -----------------------------------------------------------------------
    def check_interval(self, key: str, min_interval_s: float) -> float:
        """0.0 and the call is accepted, or the seconds still to wait (the call is not recorded)."""
        now = self._clock()
        entry = self._entry(key)
        elapsed = now - entry.last_call
        if elapsed < min_interval_s:
            return round(min_interval_s - elapsed, 2)
        entry.last_call = now
        entry.interval_s = min_interval_s
        return 0.0

    # -- sliding window -------------------------------------------------------------------------
    def hit(self, key: str, limit: int, window_s: float, *, count_denied: bool = True) -> bool:
        """True when this attempt is within `limit` per `window_s`. By default every attempt counts,
        allowed or not, so a client that keeps hammering stays blocked; `count_denied=False` counts
        only the accepted ones."""
        now = self._clock()
        entry = self._entry(key)
        entry.hits = [t for t in entry.hits if now - t < window_s]
        entry.window_s = window_s
        allowed = len(entry.hits) < limit
        if allowed or count_denied:
            entry.hits.append(now)
        return allowed

    # -- failure lockout ------------------------------------------------------------------------
    def locked_for(self, key: str) -> float:
        """Seconds left on this key's lockout, 0.0 when it is not locked."""
        entry = self._entries.get(key)
        if entry is None:
            return 0.0
        return max(0.0, round(entry.lock_until - self._clock(), 2))

    def record_failure(self, key: str) -> None:
        now = self._clock()
        entry = self._entry(key)
        entry.fails = [t for t in entry.fails if now - t < self._fail_window_s]
        entry.fails.append(now)
        if len(entry.fails) >= self._fail_limit:
            lock_s = min(self._base_lock_s * (2 ** entry.lock_level), self._max_lock_s)
            entry.lock_until = now + lock_s
            entry.lock_level += 1
            entry.fails = []

    def record_success(self, key: str) -> None:
        entry = self._entries.get(key)
        if entry is not None:
            entry.fails = []
            entry.lock_until = 0.0
            entry.lock_level = 0

    @staticmethod
    def retry_after(seconds: float) -> str:
        """The Retry-After header value: whole seconds, rounded up, at least 1."""
        return str(max(1, math.ceil(seconds)))
