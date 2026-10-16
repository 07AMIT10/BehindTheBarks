import struct
from pathlib import Path
from starlette.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def make_dummy_wav(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    num_samples = int(duration_s * sample_rate)
    pcm_bytes = b"\x00\x00" * num_samples
    subchunk2_size = len(pcm_bytes)
    chunk_size = 36 + subchunk2_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,  # subchunk1 size
        1,   # PCM format
        1,   # 1 channel (mono)
        sample_rate,
        sample_rate * 2,  # byte rate
        2,   # block align
        16,  # bits per sample
        b"data",
        subchunk2_size,
    )
    return header + pcm_bytes


def test_audio_snippet_upload_and_download(tmp_path: Path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app) as client:
        wav = make_dummy_wav(0.5)

        # Missing header
        r = client.post("/events/audio-snippet", content=wav)
        assert r.status_code == 400

        # Invalid payload (not RIFF)
        r = client.post("/events/audio-snippet", headers={"X-Snippet-ID": "snip_test_1"}, content=b"not a wav file header test")
        assert r.status_code == 400

        # Successful upload
        snip_id = "snip_bark_1001"
        r = client.post("/events/audio-snippet", headers={"X-Snippet-ID": snip_id}, content=wav)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["snippet_id"] == snip_id

        # Retrieve snippet
        r_get = client.get(f"/snippets/{snip_id}")
        assert r_get.status_code == 200
        assert r_get.headers["content-type"] == "audio/wav"
        assert r_get.content == wav

        # Not found
        r_not_found = client.get("/snippets/non_existent_snip")
        assert r_not_found.status_code == 404
