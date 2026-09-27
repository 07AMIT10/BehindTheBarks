import json
import threading
import time

import cv2
import numpy as np
import pytest
import uvicorn
from websockets.exceptions import ConnectionClosed

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config
from scripts.ingest_client import (audio_message, encode_jpeg, frame_message, hello_message, run, sine_chunk,
                                   synthetic_frame)


class NoLLM:
    enabled = False
    last_call = None


def test_message_framing_and_hello():
    assert frame_message(b"\xff\xd8x") == b"\x01\xff\xd8x"
    assert audio_message(b"\x00\x01") == b"\x02\x00\x01"
    h = json.loads(hello_message(360, 640, 8, 48000, "bot", camera=False))
    assert h == {"type": "hello", "width": 360, "height": 640, "fps": 8, "sample_rate": 48000, "device": "bot",
                 "facing": "back", "camera": False}


def test_sine_chunk_is_int16_le_and_continuous():
    a = np.frombuffer(sine_chunk(48000, 0, 4800), "<i2")
    b = np.frombuffer(sine_chunk(48000, 4800, 4800), "<i2")
    assert a.shape == (4800,) and 6000 < a.max() <= 32767 * 0.2 + 1
    whole = np.frombuffer(sine_chunk(48000, 0, 9600), "<i2")
    assert np.array_equal(np.concatenate([a, b]), whole)


def test_encode_jpeg_long_side_and_portrait():
    jpeg = encode_jpeg(synthetic_frame(3, 1280, 720), portrait=True)
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert jpeg[:2] == b"\xff\xd8" and img.shape[:2] == (640, 360)


@pytest.fixture
def server(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    mp = MockPipeline(cfg)
    app = create_app(cfg, pipeline=mp, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True)
    th.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    port = srv.servers[0].sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}/ingest", app, mp
    srv.should_exit = True
    th.join(5)


def test_run_streams_to_a_real_server(server):
    url, app, mp = server
    sent = run(url, fps=10, duration=1.0, quiet=True)
    assert sent["frames"] >= 8 and sent["audio"] >= 8
    assert mp.latest_frame_jpeg() is not None
    assert mp.status()["source"] == "browser" and mp.status()["audio_ok"] is True


def test_run_reports_busy_close(server):
    url, app, mp = server
    done = threading.Event()
    th = threading.Thread(target=lambda: (run(url, fps=5, duration=1.5, quiet=True), done.set()))
    th.start()
    time.sleep(0.4)
    with pytest.raises(ConnectionClosed) as exc:
        run(url, fps=5, duration=1.0, quiet=True)
    assert exc.value.rcvd.code == 4409
    th.join(5)
    assert done.is_set()
