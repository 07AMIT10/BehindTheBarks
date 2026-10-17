import base64
from pathlib import Path
from starlette.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def test_voice_chunk_endpoint(tmp_path: Path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    pipe = MockPipeline(cfg)  # same instance must be handed to the app below
    app = create_app(cfg, pipeline=pipe, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app) as client:
        # 1. Missing body
        r = client.post("/voice/chunk", json={})
        assert r.status_code == 400

        # 2. Oversized body
        huge_pcm = "A" * 400_000
        r = client.post("/voice/chunk", json={"pcm": huge_pcm})
        assert r.status_code == 413

        # 3. Valid voice chunk
        dummy_pcm = b"\x00\x00" * 1600  # 100ms of 16kHz mono 16-bit
        b64 = base64.b64encode(dummy_pcm).decode("ascii")
        r = client.post("/voice/chunk", json={"pcm": b64})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        # `sent` alone is a constant (MockPipeline.send_voice returns True even if the server dropped
        # the chunk); the real observable is the mock recording the exact base64 it was handed.
        assert data["sent"] is True
        assert pipe._last_voice == b64
