"""Deliberate stop over the real WebSocket: the phone says bye, the dashboard goes idle.

The two ways a session ends look identical on the wire today (close code 1005 for both), so these
tests pin the split: `bye` (or a clean 1000 close) is a stop -> the phone is forgotten and the
pipeline reports "idle"; anything else is a drop -> the disconnected snapshot stays and the pipeline
stalls, which is what the "Reconnecting to camera" notice is for.

NOTE: /status is polled inside the TestClient context on purpose: leaving the `with c:` block runs
the app's lifespan shutdown, which stops the pipeline (status "stopped").
"""

import json
import time

from backend.web.ingest_events import KIND_PREVIEW

from .test_ingest_events import envelope, frame_data, hello_msg, phone_jpeg, remote_client, wait_for

BYE = json.dumps({"type": "bye"})


def handshake(ws):
    """Complete the hello handshake on an entered socket and stream one frame + preview."""
    ws.send_text(hello_msg(phone_time=time.time()))
    assert ws.receive_json()["type"] == "hello_ack"
    ws.send_text(envelope("frame", frame_data(time.time())))
    ws.send_bytes(bytes([KIND_PREVIEW]) + phone_jpeg())


def test_stop_with_bye_leaves_the_server_idle_never_stalled(tmp_path):
    c, p = remote_client(tmp_path)
    with c, c.websocket_connect("/ingest-events") as ws:
        handshake(ws)
        assert wait_for(lambda: p.status()["state"] == "running")
        ws.send_text(BYE)
        ws.close()  # the app's close(1000) right after bye
        states = set()
        deadline = time.time() + 3.0  # 1.5x stale_s: long enough for the old bug to show 'stalled'
        while time.time() < deadline:
            st = c.get("/status").json()
            assert st["phone"] is None
            states.add(st["pipeline_status"]["state"])
            time.sleep(0.05)
        assert states == {"idle"}
        assert p.latest_frame_jpeg() is None


def test_clean_close_without_bye_is_still_a_stop(tmp_path):
    c, p = remote_client(tmp_path)
    with c, c.websocket_connect("/ingest-events") as ws:
        handshake(ws)
        assert wait_for(lambda: p.status()["state"] == "running")
        ws.close(code=1000)  # an older app build: no bye, but a deliberate close
        st = c.get("/status").json()
        assert st["phone"] is None
        assert st["pipeline_status"]["state"] == "idle"


def test_abrupt_close_without_bye_keeps_a_disconnected_snapshot(tmp_path):
    c, p = remote_client(tmp_path, **{"remote": {"stale_s": 0.2}})
    with c, c.websocket_connect("/ingest-events") as ws:
        handshake(ws)
        assert wait_for(lambda: p.status()["state"] == "running")
        ws.close(code=1006)  # TCP drop / killed app: no close frame, no bye
        st = c.get("/status").json()
        phone = st["phone"]
        assert phone is not None and phone["connected"] is False
        assert wait_for(lambda: p.status()["state"] == "stalled", timeout=2.0)  # and only now
