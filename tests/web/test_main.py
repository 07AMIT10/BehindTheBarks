from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def client(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["video"]["fps"] = 50
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return TestClient(app)


def test_health_and_status(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/health").json() == {"ok": True}
        s = c.get("/status").json()
        assert s["pipeline"] == "mock" and s["llm"]["enabled"] is False


def test_ws_receives_status_then_live_types_and_treat(tmp_path):
    with client(tmp_path) as c, c.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        c.post("/treat")  # jump to excited -> yips
        seen = set()
        for _ in range(400):
            seen.add(ws.receive_json()["type"])
            if {"frame", "rules", "emotion", "treat", "audio"} <= seen:
                break
        assert {"frame", "rules", "emotion", "treat", "audio"} <= seen


def test_treat_recorded_in_events(tmp_path):
    with client(tmp_path) as c:
        ts = c.post("/treat").json()["ts"]
        ev = c.get("/events").json()
        assert any(m["type"] == "treat" and m["data"]["ts"] == ts for m in ev["events"])
        assert isinstance(ev["timeline"], list)


def test_video_is_multipart_jpeg(tmp_path):
    with client(tmp_path) as c:
        r = c.get("/video?max_frames=2")
        assert r.headers["content-type"].startswith("multipart/x-mixed-replace")
        assert r.content.count(b"--frame") == 2 and b"\xff\xd8" in r.content


def test_session_log_written(tmp_path):
    with client(tmp_path) as c:
        c.post("/treat")
    logs = list((tmp_path / "out").glob("session-*.jsonl"))
    assert logs and '"type":"treat"' in logs[0].read_text()
