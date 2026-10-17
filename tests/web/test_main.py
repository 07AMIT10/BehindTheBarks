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
        # two ticks, but the frame is unchanged: it is written once (unchanged JPEGs are not re-sent)
        assert r.content.count(b"--frame") == 1 and b"\xff\xd8" in r.content


def test_session_log_written(tmp_path):
    with client(tmp_path) as c:
        c.post("/treat")
    logs = list((tmp_path / "out").glob("session-*.jsonl"))
    assert logs and '"type":"treat"' in logs[0].read_text()


def test_apk_and_download_routes(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    frontend_out = root / "frontend" / "out"
    apk_candidates = [
        frontend_out / "wagwatch.apk",
        frontend_out / "app-debug.apk",
        frontend_out / "download" / "WagWatch.apk",
        root / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk",
    ]
    apk_exists = any(p.is_file() for p in apk_candidates)

    with client(tmp_path) as c:
        if (frontend_out / "download.html").is_file():
            r_dl = c.get("/download")
            assert r_dl.status_code == 200
            assert "WagWatch" in r_dl.text

        r_apk = c.get("/wagwatch.apk")
        r_dl_apk = c.get("/download/apk")
        r_debug = c.get("/app-debug.apk")
        if apk_exists:
            assert r_apk.status_code == 200
            assert r_apk.headers["content-type"] == "application/vnd.android.package-archive"
            assert 'filename="WagWatch.apk"' in r_apk.headers["content-disposition"]
            assert "no-cache" in r_apk.headers.get("cache-control", "")
            assert r_dl_apk.status_code == 200
            assert r_debug.status_code == 200
        else:
            assert r_apk.status_code == 404
            assert r_dl_apk.status_code == 404
            assert r_debug.status_code == 404


def test_version_and_build_id(tmp_path):
    with client(tmp_path) as c:
        v = c.get("/version").json()
        assert "build_id" in v
        assert "server_origin" in v
        assert "fallback" in v

        s = c.get("/status").json()
        assert "build_id" in s


def test_static_cache_control_headers(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    frontend_out = root / "frontend" / "out"

    with client(tmp_path) as c:
        if (frontend_out / "index.html").is_file():
            r = c.get("/")
            assert r.status_code == 200
            assert "no-cache" in r.headers.get("cache-control", "")
            assert "no-store" in r.headers.get("cache-control", "")

        # Check an asset under _next/static if any exist
        static_chunks = list((frontend_out / "_next" / "static").glob("**/*.js")) if (frontend_out / "_next" / "static").is_dir() else []
        if static_chunks:
            rel = static_chunks[0].relative_to(frontend_out)
            r_static = c.get(f"/{rel}")
            if r_static.status_code == 200:
                assert "immutable" in r_static.headers.get("cache-control", "")


