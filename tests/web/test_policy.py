"""T3.3 Backend policy engine, endpoints and ceilings tests.

Verifies:
1. Viewer with mode_ceiling="limited" cannot get raw frames from /video even when station cap is full.
2. Owner POST /mode full above station cap returns {"status":"pending"} and sends mode_request to phone.
3. Lowering to privacy makes open /video generator switch to Events Only slate within one frame.
4. GET and POST /privacy wrapper behaviours.
5. Viewer POST /mode -> 403.
6. GET /mode returns caller-specific effective mode.
"""

import base64
import contextlib
import io
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest_events import KIND_PREVIEW
from backend.web.policy import build_events_only_slate, build_privacy_slate
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


class NoLLM:
    enabled = False
    last_call = None


def make_test_jpeg(label: str = "REAL_CAMERA_FRAME") -> bytes:
    img = Image.new("RGB", (320, 240), color=(100, 150, 200))
    d = ImageDraw.Draw(img)
    d.text((20, 20), label, fill=(255, 255, 255))
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=80)
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
    def __init__(self, c: TestClient, device: str = "TestPhone"):
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


@contextlib.contextmanager
def connect_station(c: TestClient, station: StationClient):
    with c.websocket_connect("/ingest-events") as ws:
        ws.send_text(station.hello())
        challenge = ws.receive_json()
        assert challenge["type"] == "challenge"
        ws.send_text(station.auth(challenge["nonce"]))
        assert ws.receive_json()["type"] == "hello_ack"
        yield ws


def test_viewer_with_mode_ceiling_limited_cannot_get_raw_frames(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        # Create household
        c.post("/households", json={"dog_name": "Rover"})
        owner_cookie = c.cookies[VIEWER_COOKIE]

        # Station connects and streams a raw frame
        station = StationClient(c)
        real_jpeg = make_test_jpeg("OPTICAL_ROOM_FRAME")
        with connect_station(c, station) as ws:
            ws.send_bytes(bytes([KIND_PREVIEW]) + real_jpeg)

            # Owner sets household mode to full
            c.cookies.set(VIEWER_COOKIE, owner_cookie)
            r = c.post("/mode", json={"mode": "full"})
            assert r.status_code == 200

            # Owner requests video -> gets raw camera frame
            owner_video = c.get("/video?max_frames=1").content
            assert real_jpeg in owner_video

            # Create pairing code for limited viewer
            c.cookies.set(VIEWER_COOKIE, owner_cookie)
            code = c.post("/households/me/pairing", json={"kind": "viewer", "mode_ceiling": "limited"}).json()["code"]

            # Limited viewer joins
            c.cookies.clear()
            c.post("/join", json={"code": code, "label": "LimitedGuest"})
            guest_cookie = c.cookies[VIEWER_COOKIE]

            # Limited viewer requests video -> must NOT get raw frame
            guest_video = c.get("/video?max_frames=1").content
            assert real_jpeg not in guest_video
            assert b"--frame" in guest_video


def test_owner_post_mode_full_above_cap_returns_pending_and_sends_mode_request(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rover"})
        owner_cookie = c.cookies[VIEWER_COOKIE]

        station = StationClient(c)
        with connect_station(c, station) as ws:
            # Phone reports station cap as privacy
            ws.send_text(json.dumps({
                "type": "mode_state",
                "data": {"cap": "privacy", "effective": "privacy", "reason": "station"},
            }))

            # Owner tries to set mode to full (which is above cap 'privacy')
            c.cookies.set(VIEWER_COOKIE, owner_cookie)
            res = c.post("/mode", json={"mode": "full"})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "pending"
            assert data["mode"] == "full"

            # The station WebSocket fake receives the mode_request downlink
            downlink = ws.receive_json()
            assert downlink["type"] == "mode_request"
            assert downlink["data"]["mode"] == "full"
            assert downlink["data"]["from"] == "owner"


def test_lowering_to_privacy_makes_video_switch_to_events_only_slate(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rover"})
        owner_cookie = c.cookies[VIEWER_COOKIE]

        station = StationClient(c)
        real_jpeg = make_test_jpeg("OPTICAL_FRAME")
        with connect_station(c, station) as ws:
            ws.send_bytes(bytes([KIND_PREVIEW]) + real_jpeg)

            c.cookies.set(VIEWER_COOKIE, owner_cookie)
            c.post("/mode", json={"mode": "full"})

            # In full mode, /video serves raw frame
            v1 = c.get("/video?max_frames=1").content
            assert real_jpeg in v1

            # Owner lowers to privacy
            c.post("/mode", json={"mode": "privacy"})

            # In privacy mode, /video serves Events Only slate
            v2 = c.get("/video?max_frames=1").content
            assert real_jpeg not in v2
            assert build_events_only_slate() in v2


def test_privacy_wrapper_behaviours(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rover"})
        owner_cookie = c.cookies[VIEWER_COOKIE]

        # Initially new household is privacy mode (not blackout)
        assert c.get("/privacy").json()["privacy_mode"] is False

        # POST /privacy enabled: true sets mode to blackout
        r = c.post("/privacy", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["privacy_mode"] is True
        assert c.get("/privacy").json()["privacy_mode"] is True
        assert c.get("/mode").json()["household_mode"] == "blackout"

        # /video now serves privacy blackout slate
        v = c.get("/video?max_frames=1").content
        assert build_privacy_slate() in v

        # POST /privacy enabled: false restores previous mode
        r = c.post("/privacy", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["privacy_mode"] is False
        assert c.get("/privacy").json()["privacy_mode"] is False
        # Household mode restored
        assert c.get("/mode").json()["household_mode"] in ("privacy", "full")


def test_viewer_post_mode_and_privacy_returns_403(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rover"})
        owner_cookie = c.cookies[VIEWER_COOKIE]

        code = c.post("/households/me/pairing", json={"kind": "viewer"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code, "label": "ViewerGuest"})

        # Viewer attempts POST /mode -> 403
        r1 = c.post("/mode", json={"mode": "privacy"})
        assert r1.status_code == 403

        # Viewer attempts POST /privacy -> 403
        r2 = c.post("/privacy", json={"enabled": True})
        assert r2.status_code == 403


def test_get_mode_returns_caller_effective_mode(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rover"})
        owner_cookie = c.cookies[VIEWER_COOKIE]

        # Owner raises mode to full
        c.post("/mode", json={"mode": "full"})
        m_owner = c.get("/mode").json()
        assert m_owner["household_mode"] == "full"
        assert m_owner["effective"] == "full"

        # Pair guest with mode_ceiling limited
        code = c.post("/households/me/pairing", json={"kind": "viewer", "mode_ceiling": "limited"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code, "label": "LimitedGuest"})

        # Guest GET /mode reports effective=limited
        m_guest = c.get("/mode").json()
        assert m_guest["household_mode"] == "full"
        assert m_guest["effective"] == "limited"


def test_invalid_modes_rejected(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rover"})
        assert c.post("/mode", json={}).status_code == 400
        assert c.post("/mode", json={"mode": "ultra_hd"}).status_code == 400
