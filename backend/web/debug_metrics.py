"""Server debug metrics, event loop lag measurement, and battery drain rate (T6.3)."""

from __future__ import annotations

import asyncio
import time
from typing import Any


def calculate_drain_pct_per_h(samples: list[dict[str, Any]], now: float | None = None) -> float | None:
    """Calculate battery drain rate (% per hour) via ordinary least squares over the last 30 minutes.

    Returns:
    - None if samples are empty, or the latest sample is charging, or fewer than 2 unplugged
      samples span >= 30 seconds.
    - Positive float (or 0.0) rounded to 2 decimals representing % discharge per hour.
    """
    if not samples:
        return None

    latest = samples[-1]
    if latest.get("charging"):
        return None

    now_ts = now if now is not None else time.time()
    cutoff = now_ts - 1800.0  # 30 minutes

    unplugged = [
        s for s in samples
        if not s.get("charging") and s.get("battery_pct") is not None and s.get("ts", 0) >= cutoff
    ]

    if len(unplugged) < 2:
        return None

    t_values = [float(s["ts"]) for s in unplugged]
    y_values = [float(s["battery_pct"]) for s in unplugged]

    span_s = max(t_values) - min(t_values)
    if span_s < 30.0:
        return None

    n = len(t_values)
    mean_t = sum(t_values) / n
    mean_y = sum(y_values) / n

    denom = sum((t - mean_t) ** 2 for t in t_values)
    if denom == 0:
        return None

    numer = sum((t - mean_t) * (y - mean_y) for t, y in zip(t_values, y_values))
    slope = numer / denom  # % per second

    # Drain is positive rate of battery loss
    drain_per_h = -slope * 3600.0
    return round(max(0.0, drain_per_h), 2)


def collect_server_metrics(uptime_s: float, loop_lag_ms: float) -> dict[str, Any]:
    """Collect current process resource metrics (RSS MB, CPU %, threads, uptime, lag)."""
    try:
        import psutil
        proc = psutil.Process()
        mem = proc.memory_info()
        rss_mb = round(mem.rss / (1024 * 1024), 1)
        cpu_pct = round(proc.cpu_percent(interval=None), 1)
        threads = proc.num_threads()
    except Exception:
        rss_mb = 0.0
        cpu_pct = 0.0
        threads = 1

    return {
        "rss_mb": rss_mb,
        "cpu_pct": cpu_pct,
        "threads": threads,
        "uptime_s": round(uptime_s, 1),
        "loop_lag_ms": round(loop_lag_ms, 2),
    }


class LoopLagMonitor:
    """Measures event loop lag by recording asyncio.sleep overshoot."""

    def __init__(self, interval_s: float = 0.5) -> None:
        self.interval_s = interval_s
        self.lag_ms: float = 0.0
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while self._running:
            t0 = time.monotonic()
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                break
            overshoot = (time.monotonic() - t0 - self.interval_s) * 1000.0
            self.lag_ms = max(0.0, round(overshoot, 2))

    def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
