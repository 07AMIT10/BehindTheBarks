"""Peek gating by household mode (privacy fix): POST /peek is part of the `limited` contract only.

Verifies:
1. POST /peek returns 409 in `privacy` and `blackout` households (detail.error == "household_mode").
2. POST /peek works in a `limited` household with a full station cap.
3. While NOT limited, a peek window never raises the household mode (GET /mode stays
   household_mode and /video serves the appropriate slate, never raw frames).
4. Setting a mode clears every active peek window of the household (no ghost lift afterwards).
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import time

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest_events import KIND_PREVIEW
from backend.web.policy import build_events_only_slate, build_privacy_slate
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


class NoLLM:
    enabled = False
    last_call = None


def _make_jpeg(label: str = "RAW_ROOM_FRAME") -> bytes:
    img = Image.new("RGB", (320, 240), color=(90, 120, 170))
    d = ImageDraw.Draw(img)
    d.text((20, 20), label, fill=(255, 255, 255))
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=80)
    return b.getvalue()


class StationClient:
    def __init__(self, c: TestClient, device: str = "TestPhone"):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        self.key = ec.generate_private_key(ec.SECP256R1())
        self.device = device
        code = c.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        spki = base64.b64encode(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode()
        r = c.post("/pair", json={"code": code, "pubkey_spki_b64": spki, "device_name": device}).json()
        self.station_id, self.household_id = r["station_id"], r["household_id"]

    def hello(self) -> str:
        return json.dumps({"type": "hello", "proto": 2, "station_id": self.station_id,
                           "device": self.device, "phone_time": time.time(), "models": {},
                           "profile": "mobile"})

    def auth(self, nonce_b64: str) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec

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


def app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 20
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def _create_household_and_get_runtime(c: TestClient) -> tuple[str, object]:
    hid = c.post("/households", json={"dog_name": "Rover"}).json()["household_id"]
    c.get("/mode")  # materialises the household's registry entry
    return hid, c.app if not hasattr(c, "app") else None  # placeholder, replaced below


def _make_app_and_household(c: TestClient, app_) -> tuple[str, object]:
    hid = c.post("/households", json={"dog_name": "Rover"}).json()["household_id"]
    c.get("/mode")  # triggers registry.get(hid) inside the request handler
    lr = app_.state.registry.peek(hid)
    assert lr is not None
    return hid, lr


def _install_clock(lr, sim_time) -> None:
    """Deterministic peek expiry for every channel of the household."""
    def clock() -> float:
        return sim_time[0]

    for ch in lr.channels.values():
        ch.limited.clock = clock


def test_peek_refused_in_privacy_and_blackout(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        hid, lr = _make_app_and_household(c, app_)
        assert c.post("/mode", json={"mode": "full"}).status_code == 200  # full is also refused
        r_full = c.post("/peek")
        assert r_full.status_code == 409
        assert r_full.json()["detail"]["error"] == "household_mode"
        for mode in ("privacy", "blackout"):
            assert c.post("/mode", json={"mode": mode}).status_code == 200
            r = c.post("/peek")
            assert r.status_code == 409, mode
            assert r.json()["detail"]["error"] == "household_mode"


def test_peek_allowed_and_effective_in_limited(tmp_path):
    app_ = app(tmp_path)
    sim_time = [1000.0]
    with TestClient(app_) as c:
        hid, lr = _make_app_and_household(c, app_)
        _install_clock(lr, sim_time)
        lr.pipeline.ingest_frame(_make_jpeg(), 1000.0)

        assert c.post("/mode", json={"mode": "limited"}).status_code == 200
        r = c.post("/peek")
        assert r.status_code == 200
        assert r.json()["status"] == "peeking"

        sim_time[0] = 1005.0  # inside the 15s window: the caller reaches full
        assert c.get("/mode").json()["effective"] == "full"

        sim_time[0] = 1020.0  # after 15s it reverts to limited
        assert c.get("/mode").json()["effective"] == "limited"


def test_stale_peek_never_raises_a_limited_mode(tmp_path):
    """A window that was granted while the household was limited must not lift `privacy`"""
    app_ = app(tmp_path)
    sim_time = [1000.0]
    with TestClient(app_) as c:
        hid, lr = _make_app_and_household(c, app_)
        _install_clock(lr, sim_time)
        ch = lr.get_channel(None)

        assert c.post("/mode", json={"mode": "privacy"}).status_code == 200
        # simulate a window that predates no-clearing paths: granted earlier, still live
        ch.limited.start_peek("owner", role="owner", duration_s=15.0)
        sim_time[0] = 1005.0

        m = c.get("/mode").json()
        assert m["household_mode"] == "privacy"
        assert m["effective"] == "privacy"  # not "full"

        video = c.get("/video?max_frames=1").content
        assert b"RAW_ROOM_FRAME" not in video
        assert build_events_only_slate() in video


def test_mode_set_clears_every_active_peek(tmp_path):
    app_ = app(tmp_path)
    sim_time = [1000.0]
    with TestClient(app_) as c:
        hid, lr = _make_app_and_household(c, app_)
        _install_clock(lr, sim_time)
        ch = lr.get_channel(None)

        assert c.post("/mode", json={"mode": "limited"}).status_code == 200
        assert c.post("/peek").status_code == 200
        sim_time[0] = 1005.0
        assert c.get("/mode").json()["effective"] == "full"  # the window is live

        assert c.post("/mode", json={"mode": "privacy"}).status_code == 200
        assert c.get("/mode").json()["effective"] == "privacy"  # the window did not lift privacy
        # no ghost window resurrects full after raising the mode again
        assert c.post("/mode", json={"mode": "limited"}).status_code == 200
        assert c.get("/mode").json()["effective"] == "limited"
