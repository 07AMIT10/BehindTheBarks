"""Household bootstrap, sessions and profile (T2.3): households-mode endpoints and cookies.

`pin` mode (the default) returns 404 for these routes and keeps every legacy test's behaviour.
"""

import time

import pytest
from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config

VIEWER_TOKEN_COOKIE = "btb_session"


class NoLLM:
    enabled = False
    last_call = None


def households_app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return app


def pin_app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return app


def test_auth_status_reports_the_mode(tmp_path):
    with TestClient(households_app(tmp_path)) as c:
        assert c.get("/auth/status").json() == {"enabled": False, "auth_required": True, "mode": "households"}
    with TestClient(pin_app(tmp_path)) as c:
        assert c.get("/auth/status").json()["mode"] == "pin"


def test_pin_mode_household_routes_are_404(tmp_path):
    with TestClient(pin_app(tmp_path)) as c:
        assert c.post("/households", json={}).status_code == 404
        assert c.get("/me").status_code == 404
        assert c.post("/auth/recover", json={"recovery_key": "x"}).status_code == 404


def test_create_household_sets_a_cookie_and_me_works(tmp_path):
    app = households_app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/households", json={"dog_name": "Rex", "location": "Living room"})
        assert r.status_code == 200
        body = r.json()
        assert body["household_id"] and body["recovery_key"]
        assert VIEWER_TOKEN_COOKIE in r.headers.get("set-cookie", "")
        me = c.get("/me")
        assert me.status_code == 200
        assert me.json()["role"] == "owner" and me.json()["profile"]["dog_name"] == "Rex"
        st = c.get("/status").json()
        assert st["profile"]["dog_name"] == "Rex"
        # the cookie alone authenticates; no token in the URL is needed
        assert "token" not in (r.headers.get("set-cookie") or "")


def test_create_is_rate_limited_per_ip(tmp_path):
    app = households_app(tmp_path)
    with TestClient(app) as c:
        codes = [c.post("/households", json={}).status_code for _ in range(6)]
        assert codes[:5] == [200] * 5 and codes[5] == 429


def test_recovery_key_roundtrip_and_bad_key(tmp_path):
    app = households_app(tmp_path)
    with TestClient(app) as c:
        key = c.post("/households", json={"dog_name": "Rex"}).json()["recovery_key"]
        assert c.post("/auth/recover", json={"recovery_key": "wrong"}).status_code == 401
        c.cookies.clear()
        r = c.post("/auth/recover", json={"recovery_key": key})
        assert r.status_code == 200 and VIEWER_TOKEN_COOKIE in r.headers.get("set-cookie", "")
        assert c.get("/me").json()["profile"]["dog_name"] == "Rex"


def test_me_without_a_session_is_401(tmp_path):
    with TestClient(households_app(tmp_path)) as c:
        assert c.get("/me").status_code == 401


def test_profile_update_is_published(tmp_path):
    app = households_app(tmp_path)
    with TestClient(app) as c:
        c.post("/households", json={"dog_name": "Rex"})
        with c.websocket_connect("/ws", cookies={VIEWER_TOKEN_COOKIE: c.cookies[VIEWER_TOKEN_COOKIE]}) as dash:
            dash.receive_json()
            r = c.put("/households/me/profile", json={"dog_name": "Buddy", "location": "Kitchen"})
            assert r.status_code == 200 and r.json()["dog_name"] == "Buddy"
            deadline = time.time() + 3.0
            while time.time() < deadline:
                msg = dash.receive_json()
                if (msg["type"] == "status" and (msg["data"].get("profile") or {}).get("dog_name") == "Buddy"):
                    break
            else:
                raise AssertionError("profile update was not published")
            assert c.get("/status").json()["profile"]["dog_name"] == "Buddy"


def test_profile_update_rejects_overlong_fields(tmp_path):
    app = households_app(tmp_path)
    with TestClient(app) as c:
        c.post("/households", json={})
        assert c.put("/households/me/profile", json={"dog_name": "x" * 49}).status_code == 400


def test_logout_clears_the_session(tmp_path):
    app = households_app(tmp_path)
    with TestClient(app) as c:
        c.post("/households", json={})
        r = c.post("/auth/logout")
        assert r.status_code == 200
        assert c.get("/me").status_code == 401


def test_delete_household_needs_the_recovery_key_and_erases(tmp_path):
    app = households_app(tmp_path)
    with TestClient(app) as c:
        body = c.post("/households", json={"dog_name": "Rex"})
        key, hid = body.json()["recovery_key"], body.json()["household_id"]
        assert c.request("DELETE", "/households/me", json={"recovery_key": "wrong"}).status_code == 400
        r = c.request("DELETE", "/households/me", json={"recovery_key": key})
        assert r.status_code == 200 and r.json() == {"deleted": True}
        # the runtime is evicted and the old cookie is dead
        assert app.state.registry.peek(hid) is None
        c.cookies.clear()
        assert c.get("/me").status_code == 401
