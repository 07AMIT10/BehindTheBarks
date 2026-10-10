"""Tests for debug metrics endpoint and calculations (T6.3)."""

import pytest
from fastapi.testclient import TestClient

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.debug_metrics import calculate_drain_pct_per_h
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def make_app(tmp_path, debug_enabled: bool = True, auth_mode: str = "households"):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = auth_mode
    cfg["web"]["debug"]["enabled"] = debug_enabled
    cfg["web"]["pipeline"] = "remote"
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def test_drain_pct_per_h_calculation():
    # Empty
    assert calculate_drain_pct_per_h([]) is None

    # Charging
    now = 10000.0
    samples = [
        {"ts": now - 600, "battery_pct": 80, "charging": False},
        {"ts": now, "battery_pct": 85, "charging": True},
    ]
    assert calculate_drain_pct_per_h(samples, now=now) is None

    # Too short span (< 30s)
    samples = [
        {"ts": now - 10, "battery_pct": 80, "charging": False},
        {"ts": now, "battery_pct": 79, "charging": False},
    ]
    assert calculate_drain_pct_per_h(samples, now=now) is None

    # 5% drop in 1800s (30m) = 10.0% drop per hour
    samples = [
        {"ts": now - 1800, "battery_pct": 90.0, "charging": False},
        {"ts": now - 900, "battery_pct": 87.5, "charging": False},
        {"ts": now, "battery_pct": 85.0, "charging": False},
    ]
    rate = calculate_drain_pct_per_h(samples, now=now)
    assert rate is not None
    assert rate == pytest.approx(10.0, abs=0.1)

    # Battery percentage increasing (noisy/charging without flag) -> clamped to 0.0
    samples = [
        {"ts": now - 600, "battery_pct": 80.0, "charging": False},
        {"ts": now, "battery_pct": 82.0, "charging": False},
    ]
    assert calculate_drain_pct_per_h(samples, now=now) == 0.0


def test_debug_metrics_disabled_returns_404(tmp_path):
    app = make_app(tmp_path, debug_enabled=False)
    with TestClient(app) as c:
        # Auth status reflects debug: False
        r = c.get("/auth/status")
        assert r.status_code == 200
        assert r.json()["debug"] is False

        # Attempt to access debug endpoint
        r2 = c.get("/debug/metrics")
        assert r2.status_code == 404


def test_debug_metrics_viewer_role_returns_403(tmp_path):
    app = make_app(tmp_path, debug_enabled=True)
    with TestClient(app) as c:
        # Create household (owner)
        c.post("/households", json={"dog_name": "Buddy"})
        owner_cookie = c.cookies[VIEWER_COOKIE]

        # Invite viewer
        r_invite = c.post("/households/me/pairing", json={"kind": "viewer", "role": "viewer"})
        code = r_invite.json()["code"]

        # Redeem invite as viewer
        c2 = TestClient(app)
        r_redeem = c2.post("/join", json={"code": code, "label": "ViewerPhone"})
        assert r_redeem.status_code == 200

        # Viewer gets 403
        r_viewer = c2.get("/debug/metrics")
        assert r_viewer.status_code == 403
        assert "Owner role required" in r_viewer.json()["detail"]


def test_debug_metrics_owner_returns_valid_shape(tmp_path):
    app = make_app(tmp_path, debug_enabled=True)
    with TestClient(app) as c:
        r_auth = c.get("/auth/status")
        assert r_auth.json()["debug"] is True

        c.post("/households", json={"dog_name": "Buddy"})
        r = c.get("/debug/metrics")
        assert r.status_code == 200
        data = r.json()

        assert "server" in data
        assert "rss_mb" in data["server"]
        assert "cpu_pct" in data["server"]
        assert "threads" in data["server"]
        assert "uptime_s" in data["server"]
        assert "loop_lag_ms" in data["server"]

        assert "hub" in data
        assert "clients" in data["hub"]
        assert "queue_depths" in data["hub"]

        assert data["households"] >= 1
        assert "station" in data
        assert "downlinks" in data
        assert "sent" in data["downlinks"]
        assert "dropped" in data["downlinks"]


def test_debug_metrics_station_filtering_and_not_found(tmp_path):
    app = make_app(tmp_path, debug_enabled=True)
    with TestClient(app) as c:
        c.post("/households", json={"dog_name": "Buddy"})
        # Unknown station returns 404
        r = c.get("/debug/metrics?station=nonexistent-station")
        assert r.status_code == 404
