import time
from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest_events import PROTO
from backend.web.remote_pipeline import RemotePipeline
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def test_auth_status_and_verification(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["enabled"] = True
    cfg["web"]["auth"]["dashboard_pin"] = "1234"
    cfg["web"]["auth"]["api_token"] = "secret-token"

    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app) as c:
        # Check auth status
        r = c.get("/auth/status")
        assert r.status_code == 200
        assert r.json() == {"enabled": True, "auth_required": True}

        # Failed verify
        r = c.post("/auth/verify", json={"pin": "wrong"})
        assert r.status_code == 401

        # Successful verify with PIN
        r = c.post("/auth/verify", json={"pin": "1234"})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        token = r.json()["token"]
        assert token == "1234"

        # Protected endpoints reject without auth
        assert c.get("/status").status_code == 401
        assert c.get("/events").status_code == 401
        assert c.post("/treat").status_code == 401

        # Protected endpoints succeed with Bearer token
        headers = {"Authorization": f"Bearer {token}"}
        assert c.get("/status", headers=headers).status_code == 200
        assert c.get("/events", headers=headers).status_code == 200
        assert c.post("/treat", headers=headers).status_code == 200

        # Protected endpoints succeed with query param token
        assert c.get(f"/status?token={token}").status_code == 200

        # Protected websocket rejects without auth
        try:
            with c.websocket_connect("/ws") as ws:
                ws.receive_json()
        except Exception:
            pass  # closed with 4401

        # Protected websocket succeeds with token param
        with c.websocket_connect(f"/ws?token={token}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "status"


def test_rate_limiting_enforcement(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["enabled"] = True
    cfg["web"]["auth"]["dashboard_pin"] = "9999"

    pipe = RemotePipeline(cfg)
    app = create_app(cfg, pipeline=pipe, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    headers = {"Authorization": "Bearer 9999"}
    with TestClient(app) as c:
        # First call succeeds
        r1 = c.post("/treat", headers=headers)
        assert r1.status_code == 200

        # Second rapid call fails with 429
        r2 = c.post("/treat", headers=headers)
        assert r2.status_code == 429
        assert "Rate limit exceeded" in r2.json()["detail"]


def test_privacy_mode_and_slate(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["enabled"] = False

    pipe = RemotePipeline(cfg)
    app = create_app(cfg, pipeline=pipe, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app) as c:
        # Privacy initially False
        r = c.get("/privacy")
        assert r.status_code == 200
        assert r.json()["privacy_mode"] is False

        # Toggle privacy on
        r = c.post("/privacy", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["privacy_mode"] is True

        # Video stream now serves the privacy slate
        vr = c.get("/video?max_frames=1")
        assert vr.status_code == 200
        assert b"--frame" in vr.content


def test_ingest_token_rejection_and_acceptance(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["enabled"] = True
    cfg["web"]["auth"]["ingest_token"] = "phone-guard-token-123"

    pipe = RemotePipeline(cfg)
    app = create_app(cfg, pipeline=pipe, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    now = time.time()
    with TestClient(app) as c:
        # Connect without token -> should close with 4401
        try:
            with c.websocket_connect("/ingest-events") as ws:
                ws.send_json({"type": "hello", "proto": PROTO, "device": "TestPhone", "phone_time": now})
                ws.receive_json()
        except Exception:
            pass  # Expected disconnect

        # Connect with correct token in query param
        with c.websocket_connect("/ingest-events?token=phone-guard-token-123") as ws:
            ws.send_json({"type": "hello", "proto": PROTO, "device": "TestPhone", "phone_time": now})
            ack = ws.receive_json()
            assert ack["type"] == "hello_ack"

        # Connect with correct token in hello body
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_json({
                "type": "hello",
                "proto": PROTO,
                "device": "TestPhone",
                "phone_time": now,
                "token": "phone-guard-token-123",
            })
            ack = ws.receive_json()
            assert ack["type"] == "hello_ack"
