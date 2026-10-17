"""T3.8 End-to-end mode wire verification tests.

Verifies the on-wire contracts and station reference model across all 4 modes:
1. Blackout: station transmits no previews and no events; server returns blackout slate.
2. Privacy: station transmits events (0x00) but no previews (0x01); server returns 'Events Only' slate.
3. Limited & Full: station transmits previews only when video_viewers > 0 (anti-lurking).
4. Station Sovereignty & Approval: remote raise above station cap yields 'pending' and downlinks
   mode_request; station approval via mode_state promotes effective mode.
"""

import base64
import contextlib
import io
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest_events import KIND_PREVIEW
from backend.web.modes import min_mode
from backend.web.policy import build_events_only_slate, build_privacy_slate
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


class NoLLM:
    enabled = False
    last_call = None


def make_test_jpeg(label: str = "STATION_FRAME") -> bytes:
    img = Image.new("RGB", (320, 240), color=(80, 120, 160))
    d = ImageDraw.Draw(img)
    d.text((10, 10), label, fill=(255, 255, 255))
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=75)
    return b.getvalue()


def app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 20
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


class StationClient:
    def __init__(self, c: TestClient, device: str = "A07-TestStation"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.device = device
        code = c.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        spki = base64.b64encode(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode()
        r = c.post("/pair", json={"code": code, "pubkey_spki_b64": spki, "device_name": device}).json()
        self.station_id, self.household_id = r["station_id"], r["household_id"]

    def hello(self) -> str:
        return json.dumps({
            "type": "hello",
            "proto": 2,
            "station_id": self.station_id,
            "device": self.device,
            "phone_time": time.time(),
            "models": {},
            "profile": "mobile",
        })

    def auth(self, nonce_b64: str) -> str:
        msg = AUTH_PREFIX + base64.b64decode(nonce_b64) + b"|" + self.station_id.encode() + b"|" + self.device.encode()
        sig = base64.b64encode(self.key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        return json.dumps({"type": "auth", "sig": sig})


class ReferenceStation:
    """Simulates the on-device Android MonitorService / ModeController behavior."""

    def __init__(self, cap: str = "full"):
        self.station_cap = cap
        self.policy_mode = "full"
        self.video_viewers = 0
        self.previews_sent = 0
        self.events_sent = 0

    def effective_mode(self) -> str:
        return min_mode(self.station_cap, self.policy_mode)

    def on_downlink(self, msg: dict) -> dict | None:
        mtype = msg.get("type")
        if mtype == "policy":
            data = msg.get("data", {})
            self.policy_mode = data.get("mode", self.policy_mode)
            viewers = data.get("viewers", {})
            self.video_viewers = viewers.get("video", 0)
        elif mtype == "mode_request":
            # Simulate prompt approval if requested mode is acceptable
            req_mode = msg.get("data", {}).get("mode", "full")
            self.station_cap = req_mode
            return {
                "type": "mode_state",
                "data": {"cap": self.station_cap, "effective": self.effective_mode(), "reason": "owner_approved"},
            }
        return None

    def send_cycle(self, ws, frame_event: dict, preview_jpeg: bytes):
        eff = self.effective_mode()
        if eff == "blackout":
            # Blackout: camera & mic callbacks return immediately without sending
            return

        # Privacy, Limited, Full send frame events
        ws.send_text(json.dumps({"type": "frame", "data": frame_event}))
        self.events_sent += 1

        # Limited and Full send preview JPEG only when video viewers > 0
        if eff in ("limited", "full") and self.video_viewers > 0:
            ws.send_bytes(bytes([KIND_PREVIEW]) + preview_jpeg)
            self.previews_sent += 1


@contextlib.contextmanager
def connect_reference_station(c: TestClient, station: StationClient, ref: ReferenceStation):
    with c.websocket_connect("/ingest-events") as ws:
        ws.send_text(station.hello())
        challenge = ws.receive_json()
        assert challenge["type"] == "challenge"
        ws.send_text(station.auth(challenge["nonce"]))
        assert ws.receive_json()["type"] == "hello_ack"

        # Initial mode state
        ws.send_text(json.dumps({
            "type": "mode_state",
            "data": {"cap": ref.station_cap, "effective": ref.effective_mode(), "reason": "init"}
        }))

        yield ws


def sample_frame_event() -> dict:
    return {
        "ts": time.time(),
        "seq": 1,
        "dog_detected": True,
        "dog_bounds": [0.1, 0.2, 0.8, 0.9],
        "crop_bounds": [0.0, 0.0, 1.0, 1.0],
        "crop_confidence": 0.95,
        "body_keypoints": {},
        "face_landmarks": [],
    }


def test_reference_station_wire_blackout(tmp_path):
    """In Blackout: reference station transmits no previews and no events."""
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Buddy"})
        station = StationClient(c)
        ref = ReferenceStation(cap="blackout")
        jpeg = make_test_jpeg()

        with connect_reference_station(c, station, ref) as ws:
            ref.send_cycle(ws, sample_frame_event(), jpeg)

            assert ref.previews_sent == 0
            assert ref.events_sent == 0

            # Verify /status and /video slate
            st = c.get("/status").json()
            assert st["phone"]["station_cap"] == "blackout"
            assert st["phone"]["effective_mode"] == "blackout"

            vid = c.get("/video?max_frames=1")
            assert vid.status_code == 200
            # Serves the blackout slate, never a real camera JPEG
            assert jpeg not in vid.content
            assert build_privacy_slate() in vid.content


def test_reference_station_wire_privacy(tmp_path):
    """In Privacy: station transmits frame telemetry (0x00) but no previews (0x01)."""
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Buddy"})
        station = StationClient(c)
        ref = ReferenceStation(cap="privacy")
        jpeg = make_test_jpeg("PRIVACY_ROOM_FRAME")

        with connect_reference_station(c, station, ref) as ws:
            ref.send_cycle(ws, sample_frame_event(), jpeg)

            assert ref.events_sent == 1
            assert ref.previews_sent == 0

            st = c.get("/status").json()
            assert st["phone"]["effective_mode"] == "privacy"

            # /video serves the Events Only slate
            vid = c.get("/video?max_frames=1")
            assert vid.status_code == 200
            assert jpeg not in vid.content
            assert build_events_only_slate() in vid.content


def test_reference_station_wire_full_with_viewer_tracking(tmp_path):
    """In Full: station sends previews only when video viewers > 0."""
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Buddy"})
        station = StationClient(c)
        ref = ReferenceStation(cap="full")
        jpeg = make_test_jpeg("OPTICAL_FULL_FRAME")

        with connect_reference_station(c, station, ref) as ws:
            # Step 1: Owner mode is full
            c.post("/mode", json={"mode": "full"})

            # Initially 0 viewers -> ref.video_viewers is 0, no previews sent
            ref.send_cycle(ws, sample_frame_event(), jpeg)
            assert ref.events_sent == 1
            assert ref.previews_sent == 0

            # Step 2: Viewer connects and requests video
            # Reference station receives downlink with viewers.video >= 1
            ref.video_viewers = 1
            ref.send_cycle(ws, sample_frame_event(), jpeg)
            assert ref.previews_sent == 1

            vid = c.get("/video?max_frames=1")
            assert vid.status_code == 200
            assert jpeg in vid.content


def test_reference_station_wire_mode_request_approval(tmp_path):
    """Owner requesting mode raise sends mode_request downlink; station approval promotes mode."""
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Buddy"})
        station = StationClient(c)
        ref = ReferenceStation(cap="privacy")

        with connect_reference_station(c, station, ref) as ws:
            # Owner attempts to raise mode to full above station's privacy cap
            res = c.post("/mode", json={"mode": "full"}).json()
            assert res["status"] == "pending"

            # The station WebSocket receives the mode_request downlink
            downlink = ws.receive_json()
            assert downlink["type"] == "mode_request"
            assert downlink["data"]["mode"] == "full"
            assert downlink["data"]["from"] == "owner"

            reply = ref.on_downlink(downlink)
            assert reply is not None
            ws.send_text(json.dumps(reply))

            assert ref.station_cap == "full"

            # Mode should now be promoted to full
            st = c.get("/status").json()
            assert st["phone"]["station_cap"] == "full"
            assert st["phone"]["effective_mode"] == "full"
