"""Resource budget alerts for station thermal, battery, and stream health (T6.4)."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("resource-alerts")


def evaluate_resource_alerts(
    telemetry: dict[str, Any],
    history: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Evaluate station telemetry against resource safety thresholds.

    Alert rules:
    - temp_c >= 42.0: station high temperature
    - thermal_status >= 3: severe thermal throttling (3=THROTTLED, 4=SHUTDOWN)
    - battery_pct <= 15 and not charging: battery critically low
    - preview_dropped grows by > 20 over a 60s window (or > 20% drop rate increase)
    """
    alerts: list[str] = []

    # High temperature
    temp = telemetry.get("temp_c")
    if temp is not None and isinstance(temp, (int, float)) and temp >= 42.0:
        alerts.append(f"High temperature: {temp:.1f}°C")

    # Severe thermal throttling
    thermal_status = telemetry.get("thermal_status")
    if thermal_status is not None and isinstance(thermal_status, int) and thermal_status >= 3:
        alerts.append(f"Severe thermal throttle status {thermal_status}")

    # Low battery and discharging
    battery = telemetry.get("battery_pct")
    charging = bool(telemetry.get("charging"))
    if battery is not None and isinstance(battery, (int, float)) and battery <= 15.0 and not charging:
        alerts.append(f"Battery critically low: {round(battery)}%")

    # Preview drop spike over 60s
    curr_drops = telemetry.get("preview_dropped")
    curr_ts = telemetry.get("ts")
    if history and curr_drops is not None and curr_ts is not None and isinstance(curr_drops, (int, float)):
        # Find sample closest to 60s ago
        cutoff_ts = curr_ts - 60.0
        past_samples = [s for s in history if s.get("ts", 0) <= cutoff_ts and s.get("preview_dropped") is not None]
        if past_samples:
            past_drops = past_samples[-1].get("preview_dropped", 0)
            diff = curr_drops - past_drops
            if diff >= 20:
                alerts.append(f"High preview drop rate (+{int(diff)} dropped in 60s)")

    return alerts
