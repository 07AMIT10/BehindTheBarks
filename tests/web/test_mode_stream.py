"""Per-frame mode re-evaluation on an OPEN /video stream (the PR's producer-side privacy).

The production generator re-evaluates the effective mode every tick (backend/main.py /video
gen loop), so a mode flip while a stream is already open must take effect on the NEXT served
frame — without the client opening a new request. This is the video-side counterpart of the
audio socket, which closes mid-stream with 4412 on the same flip (test_isolation.py:446); the
video wire keeps the byte stream open and swaps what it serves (events-only slate, never the
raw phone frames that arrive mid-stream while the mode is privacy).

This starlette TestClient BUFFERS a StreamingResponse's whole body before returning, so the
test drives the raw ASGI call on the client's portal thread instead and collects response
messages as they arrive. The collector's `receive` answers the initial `http.request` and only
reports a disconnect once the body finished — an early `http.disconnect` makes the ASGI stack
cancel the unbounded stream as "client gone" and not a single frame would be observed.
"""

import base64
import json
import threading
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest_events import KIND_PREVIEW
from backend.web.policy import build_events_only_slate
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"
# A JPEG-shaped payload carrying a string that appears nowhere else on the wire.
MARKER_JPEG = b"\xff\xd8MIDSTREAMMARK\xff\xd9"


class NoLLM:
    enabled = False
    last_call = None


class Station:
    """A paired phone station, signed in exactly the way /ingest-events expects."""

    def __init__(self, client, marker: bytes, device: str):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.marker, self.device = marker, device
        code = client.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        spki = base64.b64encode(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode()
        r = client.post("/pair", json={"code": code, "pubkey_spki_b64": spki, "device_name": device}).json()
        self.station_id, self.household_id = r["station_id"], r["household_id"]

    def hello(self) -> str:
        return json.dumps({"type": "hello", "proto": 2, "station_id": self.station_id,
                           "device": self.device, "phone_time": time.time(), "models": {}, "profile": "mobile"})

    def auth(self, nonce_b64: str) -> str:
        msg = AUTH_PREFIX + base64.b64decode(nonce_b64) + b"|" + self.station_id.encode() + b"|" + self.device.encode()
        sig = base64.b64encode(self.key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        return json.dumps({"type": "auth", "sig": sig})


class BodyCollector:
    """ASGI-side sink for one streamed response; receive/send run on the portal thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.status: int | None = None
        self.parts: list[bytes] = []
        self.done = False
        self._request_read = threading.Event()

    async def receive(self):
        if not self._request_read.is_set():
            self._request_read.set()
            return {"type": "http.request", "body": b"", "more_body": False}
        import asyncio
        if not hasattr(self, "_done_evt"):
            self._done_evt = asyncio.Event()
        await self._done_evt.wait()
        return {"type": "http.disconnect"}

    async def send(self, message):
        with self.lock:
            if message.get("type") == "http.response.start":
                self.status = message.get("status")
            elif message.get("type") == "http.response.body":
                self.parts.append(message.get("body", b""))
                if not message.get("more_body", False):
                    self.done = True
                    if hasattr(self, "_done_evt"):
                        self._done_evt.set()

    def body(self) -> bytes:
        with self.lock:
            return b"".join(self.parts)

    @property
    def complete(self) -> bool:
        with self.lock:
            return self.done


def _connected_phone(ws, station: Station) -> None:
    """Run the phone handshake on an open /ingest-events socket and leave it streaming."""
    ws.send_text(station.hello())
    challenge = ws.receive_json()
    assert challenge["type"] == "challenge"
    ws.send_text(station.auth(challenge["nonce"]))
    ack = ws.receive_json()
    assert ack["type"] == "hello_ack"


def _wait_for(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _join_body_task(portal, body_task) -> None:
    """After the final body chunk is collected, reap the ASGI task loop call itself."""
    deadline = time.monotonic() + 6.0
    while not body_task.done() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert body_task.done()


def _open_video(c: TestClient, owner_cookie: str):
    """Drive GET /video as raw ASGI on the portal thread; returns (collector, task)."""
    collector = BodyCollector()
    headers = [
        (b"host", b"testserver"),
        (b"user-agent", b"pytest-mode-stream"),
        (b"cookie", f"{VIEWER_COOKIE}={owner_cookie}".encode()),
    ]
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "path": "/video",
        "raw_path": b"/video", "query_string": b"max_frames=0", "root_path": "", "scheme": "http",
        "headers": headers, "client": ("testclient", 50000), "server": ("testserver", 80),
        "extensions": {}, "state": {},
    }
    task = c.portal.start_task_soon(c.app, scope, collector.receive, collector.send)
    assert _wait_for(lambda: collector.status is not None, 5.0), "the stream never answered"
    assert collector.status == 200
    return collector, task


def test_mode_flip_is_served_by_the_already_open_stream(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 50  # 20 ms per served tick: flips land quickly and the test stays brisk
    cfg["web"]["video"]["idle_timeout_s"] = 1.5
    app = create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    slate = build_events_only_slate()

    with TestClient(app) as c:
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Rex"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        station = Station(c, MARKER_JPEG[2:-2], "PhoneOne")
        with c.websocket_connect("/ingest-events") as ws:
            _connected_phone(ws, station)

            # Stream open while the pipeline has no frame yet: 200 and then silence (no fake pixels).
            c.cookies.clear()
            c.cookies.set(VIEWER_COOKIE, owner_cookie)
            collector, task = _open_video(c, owner_cookie)
            time.sleep(0.3)
            assert MARKER_JPEG not in collector.body(), "the server must not invent a frame"

            # Owner flips to privacy MID-STREAM; the same connection starts serving the slate.
            r = c.post("/mode", json={"mode": "privacy"})
            assert r.status_code == 200
            assert _wait_for(lambda: slate in collector.body(), 5.0), (
                "a privacy flip on an open stream must reach the client without a new request")
            assert MARKER_JPEG not in collector.body(), "raw room footage must not leak behind privacy"

            # A preview pushed by the phone while privacy is on never reaches the viewer.
            ws.send_bytes(bytes([KIND_PREVIEW]) + MARKER_JPEG)
            time.sleep(0.5)
            assert MARKER_JPEG not in collector.body(), "a mid-privacy phone frame must not be forwarded"

            # Flip back to full: the stream is STILL open, and the frame data (previews) resumes.
            assert c.post("/mode", json={"mode": "full"}).status_code == 200
            assert _wait_for(lambda: MARKER_JPEG in collector.body(), 5.0), (
                "a full flip on an open stream must resume frames without a new request")
            final = collector.body()
            slate_idx, marker_idx = final.find(slate), final.find(MARKER_JPEG)
            assert -1 < slate_idx < marker_idx, "privacy slate served before any raw frame"

            # Phone hangs up: the pipeline goes idle; the stream ends on its own idle timeout.
            ws.send_text(json.dumps({"type": "bye"}))
            ws.close()
            assert _wait_for(lambda: collector.complete, 6.0), "the stream must finish, not dangle forever"
            _join_body_task(c.portal, task)
            assert task.exception() is None
