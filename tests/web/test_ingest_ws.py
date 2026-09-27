import json
import time

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


class SpyMock(MockPipeline):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.audio_calls = []

    def ingest_audio(self, pcm16, sample_rate, ts):
        self.audio_calls.append((len(pcm16), sample_rate, ts))
        super().ingest_audio(pcm16, sample_rate, ts)


def make_client(tmp_path, **ingest):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["ingest"].update(ingest)
    mp = SpyMock(cfg)
    app = create_app(cfg, pipeline=mp, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return TestClient(app), mp


def hello(**over):
    msg = {"type": "hello", "width": 360, "height": 640, "fps": 8, "sample_rate": 48000,
           "device": "Pixel 7", "facing": "back", "camera": True}
    msg.update(over)
    return json.dumps(msg)


def phone_jpeg() -> bytes:
    img = np.zeros((640, 360, 3), np.uint8)
    img[:, :, 2] = 200  # portrait, red
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes()


def wait_for(pred, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_frames_reach_pipeline_and_video(tmp_path):
    c, mp = make_client(tmp_path)
    jpeg = phone_jpeg()
    with c, c.websocket_connect("/ingest") as ws:
        ws.send_text(hello())
        before = time.time()
        for _ in range(3):
            ws.send_bytes(b"\x01" + jpeg)
        assert wait_for(lambda: mp.latest_frame_jpeg() == jpeg)
        assert before <= mp._phone_ts <= time.time()  # ts stamped at server receipt
        phone = c.get("/status").json()["phone"]
        assert phone["connected"] is True and phone["device"] == "Pixel 7" and phone["facing"] == "back"
        assert phone["camera"] is True and (phone["width"], phone["height"]) == (360, 640)
        r = c.get("/video?max_frames=1")
        assert jpeg in r.content


def test_audio_uses_hello_sample_rate(tmp_path):
    c, mp = make_client(tmp_path)
    with c, c.websocket_connect("/ingest") as ws:
        ws.send_text(hello(sample_rate=44100))
        ws.send_bytes(b"\x02" + b"\x00\x01" * 4410)
        assert wait_for(lambda: len(mp.audio_calls) == 1)
    assert mp.audio_calls[0][:2] == (8820, 44100)


def test_bad_hello_closes_4400(tmp_path):
    c, _ = make_client(tmp_path)
    with c, c.websocket_connect("/ingest") as ws:
        ws.send_text('{"type": "hello", "width": 640}')
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4400 and "hello" in exc.value.reason


def test_binary_before_hello_closes_4400(tmp_path):
    c, _ = make_client(tmp_path)
    with c, c.websocket_connect("/ingest") as ws:
        ws.send_bytes(b"\x01\xff\xd8")
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4400


def test_second_phone_rejected_4409(tmp_path):
    c, _ = make_client(tmp_path)
    with c, c.websocket_connect("/ingest") as first:
        first.send_text(hello())
        assert wait_for(lambda: c.get("/status").json()["phone"] is not None)
        with c.websocket_connect("/ingest") as second:
            second.send_text(hello(device="iPhone"))
            with pytest.raises(WebSocketDisconnect) as exc:
                second.receive_text()
        assert exc.value.code == 4409 and exc.value.reason == "Another phone is already streaming"
        assert c.get("/status").json()["phone"]["device"] == "Pixel 7"


def test_quiet_phone_replaced_4408(tmp_path):
    c, _ = make_client(tmp_path, stale_s=0.2)
    with c, c.websocket_connect("/ingest") as first:
        first.send_text(hello())
        assert wait_for(lambda: c.get("/status").json()["phone"] is not None)
        time.sleep(0.3)
        with c.websocket_connect("/ingest") as second:
            second.send_text(hello(device="iPhone"))
            with pytest.raises(WebSocketDisconnect) as exc:
                first.receive_text()
            assert exc.value.code == 4408
            assert wait_for(lambda: c.get("/status").json()["phone"]["device"] == "iPhone")


def test_dashboard_gets_phone_status_on_connect_and_disconnect(tmp_path):
    c, _ = make_client(tmp_path)
    with c, c.websocket_connect("/ws") as dash:
        assert dash.receive_json()["type"] == "status"
        with c.websocket_connect("/ingest") as ws:
            ws.send_text(hello())
            phones = []
            for _ in range(400):
                m = dash.receive_json()
                if m["type"] == "status" and "phone" in m["data"]:
                    phones.append(m["data"]["phone"])
                    break
            ws.close(1001)  # page closed or killed (the Stop button would send 1000)
        for _ in range(400):
            m = dash.receive_json()
            if m["type"] == "status" and "phone" in m["data"] and not m["data"]["phone"]["connected"]:
                phones.append(m["data"]["phone"])
                break
        assert [p["connected"] for p in phones] == [True, False]
        assert c.get("/status").json()["phone"]["connected"] is False


def test_normal_close_forgets_phone(tmp_path):
    c, _ = make_client(tmp_path)
    with c:
        with c.websocket_connect("/ingest") as ws:
            ws.send_text(hello())
            assert wait_for(lambda: c.get("/status").json()["phone"] is not None)
            ws.close(1000)
        assert wait_for(lambda: c.get("/status").json()["phone"] is None)
