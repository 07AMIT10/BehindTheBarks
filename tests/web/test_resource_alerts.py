"""Unit tests for resource budget alert rules (T6.4)."""

import pytest
from backend.web.resource_alerts import evaluate_resource_alerts


def test_temperature_alerts():
    # Normal temperature
    assert evaluate_resource_alerts({"temp_c": 35.0}) == []
    assert evaluate_resource_alerts({"temp_c": 41.9}) == []

    # High temperature threshold (>= 42.0)
    alerts = evaluate_resource_alerts({"temp_c": 42.0})
    assert len(alerts) == 1
    assert "High temperature: 42.0°C" in alerts[0]

    alerts2 = evaluate_resource_alerts({"temp_c": 45.5})
    assert len(alerts2) == 1
    assert "High temperature: 45.5°C" in alerts2[0]


def test_thermal_status_alerts():
    # Normal and moderate throttling
    assert evaluate_resource_alerts({"thermal_status": 0}) == []
    assert evaluate_resource_alerts({"thermal_status": 1}) == []
    assert evaluate_resource_alerts({"thermal_status": 2}) == []

    # Severe throttle (3 = THROTTLED, 4 = SHUTDOWN)
    alerts = evaluate_resource_alerts({"thermal_status": 3})
    assert len(alerts) == 1
    assert "Severe thermal throttle status 3" in alerts[0]

    alerts2 = evaluate_resource_alerts({"thermal_status": 4})
    assert len(alerts2) == 1
    assert "Severe thermal throttle status 4" in alerts2[0]


def test_battery_budget_alerts():
    # Healthy battery
    assert evaluate_resource_alerts({"battery_pct": 50, "charging": False}) == []
    assert evaluate_resource_alerts({"battery_pct": 16, "charging": False}) == []

    # Low battery while charging is safe (no alert)
    assert evaluate_resource_alerts({"battery_pct": 10, "charging": True}) == []

    # Low battery and discharging triggers alert
    alerts = evaluate_resource_alerts({"battery_pct": 15, "charging": False})
    assert len(alerts) == 1
    assert "Battery critically low: 15%" in alerts[0]

    alerts2 = evaluate_resource_alerts({"battery_pct": 5, "charging": False})
    assert len(alerts2) == 1
    assert "Battery critically low: 5%" in alerts2[0]


def test_preview_dropped_spike_alert():
    now = 1000.0
    history = [
        {"ts": now - 70.0, "preview_dropped": 5},
        {"ts": now - 60.0, "preview_dropped": 10},
        {"ts": now - 30.0, "preview_dropped": 15},
    ]

    # No spike (+5 in last 60s)
    current = {"ts": now, "preview_dropped": 15}
    assert evaluate_resource_alerts(current, history) == []

    # Spike (+25 in last 60s)
    current_spike = {"ts": now, "preview_dropped": 35}
    alerts = evaluate_resource_alerts(current_spike, history)
    assert len(alerts) == 1
    assert "High preview drop rate (+25 dropped in 60s)" in alerts[0]
