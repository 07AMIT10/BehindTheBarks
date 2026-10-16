import base64
from starlette.testclient import TestClient

from backend.main import create_app


def test_voice_chunk_endpoint():
    app = create_app()
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
        assert data["sent"] is True
