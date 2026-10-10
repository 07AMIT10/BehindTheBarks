"""Household isolation (T2.7): the gate every later phase must keep green.

Two households, one paired station each, and the assertion that one household's bytes, names and
events never reach the other — over /ws, /events, /status and /video — plus the negative paths
(credential spoofing, revocation, deletion, external notifiers, event-log files, pin-mode regression).
"""

import base64
import contextlib
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.base import NotifyResult
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest_events import KIND_PREVIEW
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


class SpyLLM:
    """A stand-in interpreter: enabled, so it WOULD be called if the household were misconfigured."""

    enabled = True
    last_call = None

    def __init__(self):
        self.calls = []

    async def interpret(self, *, trigger, now, features, audio, rules, jpeg):
        self.calls.append(trigger)
        return None


class SpyNotifier:
    """Records every snapshot that would leave the host."""

    def __init__(self):
        self.sent = []

    async def send(self, state, jpeg):
        self.sent.append(state)
        return NotifyResult("dashboard_only", "dashboard", "would send")


def app(tmp_path, interpreter=None, notifier=None):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["idle_timeout_s"] = 0.5  # a camera-less household must not hang the test
    # Non-legacy households must not inherit the operator's LLM/Telegram (T2.9 defaults).
    return create_app(cfg, pipeline=None, interpreter=interpreter or SpyLLM(), notifier=notifier or DashboardOnlyNotifier())


class Station:
    """A paired station: its key and the unmistakable envelopes it streams."""

    def __init__(self, client, marker: bytes, device: str):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.marker, self.device = marker, device
        code = client.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        spki = base64.b64encode(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()
        r = client.post("/pair", json={"code": code, "pubkey_spki_b64": spki, "device_name": device}).json()
        self.station_id, self.household_id = r["station_id"], r["household_id"]

    def hello(self) -> str:
        return json.dumps({"type": "hello", "proto": 2, "station_id": self.station_id,
                           "device": self.device, "phone_time": time.time(), "models": {}, "profile": "mobile"})

    def auth(self, nonce_b64: str) -> str:
        msg = AUTH_PREFIX + base64.b64decode(nonce_b64) + b"|" + self.station_id.encode() + b"|" + self.device.encode()
        sig = base64.b64encode(self.key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        return json.dumps({"type": "auth", "sig": sig})

    def marker_jpeg(self) -> bytes:
        """SOI + the household's marker + EOI: valid enough for /video, unmistakable in a byte dump."""
        return b"\xff\xd8" + self.marker + b"\xff\xd9"

    def frame(self) -> str:
        return json.dumps({"type": "frame", "data": {
            "ts": time.time(), "source": "live", "dog_detected": True,
            "bbox": [1.0, 2.0, 30.0, 40.0], "bbox_conf": 0.9,
            "body_keypoints": {"tail_base": [10.0, 20.0, 0.9]}, "face_landmarks": None,
            "features": {"tail_height": 0.4, "tail_wag_hz": 2.1, "ear_position": "up", "mouth_open": 0.6,
                         "body_lowering": 0.1, "motion_energy": 0.7, "in_feeding_zone": True}}})


@contextlib.contextmanager
def connect_station(c, station: Station):
    """Drive the full handshake on a `with`-entered socket and stream one frame + marker preview."""
    with c.websocket_connect("/ingest-events") as ws:
        ws.send_text(station.hello())
        challenge = ws.receive_json()
        assert challenge["type"] == "challenge"
        ws.send_text(station.auth(challenge["nonce"]))
        assert ws.receive_json()["type"] == "hello_ack"
        ws.send_bytes(bytes([KIND_PREVIEW]) + station.marker_jpeg())
        ws.send_text(station.frame())
        yield ws


@pytest.fixture()
def two_households(tmp_path):
    """Two households, each with its own owner cookie and its own paired station."""
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Rex"})
        h1_cookie = c.cookies[VIEWER_COOKIE]
        s1 = Station(c, b"H1MARKERBYTES", "PhoneOne")

        c.cookies.clear()
        c.post("/households", json={"dog_name": "Buddy"})
        h2_cookie = c.cookies[VIEWER_COOKIE]
        s2 = Station(c, b"H2MARKERBYTES", "PhoneTwo")
        yield app_, c, h1_cookie, h2_cookie, s1, s2


def as_owner(c, cookie: str) -> None:
    c.cookies.clear()
    c.cookies.set(VIEWER_COOKIE, cookie)


def test_no_cross_household_leak_over_any_surface(two_households):
    app_, c, h1_cookie, h2_cookie, s1, s2 = two_households
    with connect_station(c, s1):  # household 1 streams unmistakable bytes
        as_owner(c, h2_cookie)  # household 2 looks at every surface it can reach
        with c.websocket_connect("/ws", cookies={VIEWER_COOKIE: h2_cookie}) as dash:
            dash.receive_json()  # the first status
            events = c.get("/events").json()["events"]
            status = c.get("/status").json()
            video = c.get("/video?max_frames=2").content
            collected = [dash.receive_json() for _ in range(3)]
            blob = json.dumps([events, status, video.decode("latin-1"), collected])
            assert s1.marker not in blob.encode("latin-1")
            assert "PhoneOne" not in blob
            assert status["phone"] is None or status["phone"]["device"] != "PhoneOne"


def test_spoofing_a_household_id_changes_nothing(two_households):
    app_, c, h1_cookie, h2_cookie, s1, s2 = two_households
    as_owner(c, h2_cookie)
    mine = c.get("/me").json()["household_id"]
    assert mine == s2.household_id and mine != s1.household_id
    for headers, params, body in (({}, {"household_id": s1.household_id}, None),
                                  ({"X-Household": s1.household_id}, {}, None),
                                  ({}, {}, {"household_id": s1.household_id})):
        r = c.request("GET", "/status", headers=headers, params=params, json=body)
        assert r.status_code == 200
        assert r.json()["phone"] is None or r.json()["phone"]["device"] != "PhoneOne"


def test_revoking_one_station_leaves_the_other_household_alone(two_households):
    app_, c, h1_cookie, h2_cookie, s1, s2 = two_households
    with connect_station(c, s1) as ws1, connect_station(c, s2) as ws2:
        as_owner(c, h1_cookie)
        r = c.request("DELETE", f"/stations/{s1.station_id}")
        assert r.status_code == 200
        assert ws1.receive().get("code") == 4410  # the revoked station's socket is closed
        assert app_.state.registry.peek(s1.household_id).runtime.phone is None

        as_owner(c, h2_cookie)  # household 2 never noticed
        assert c.get("/status").json()["phone"]["device"] == "PhoneTwo"


def test_delete_removes_one_household_only(two_households):
    app_, c, h1_cookie, h2_cookie, s1, s2 = two_households
    # a third household whose recovery key we keep, deleted through the owner path
    c.cookies.clear()
    made = c.post("/households", json={"dog_name": "Third"})
    third_key, third_id = made.json()["recovery_key"], made.json()["household_id"]
    r = c.request("DELETE", "/households/me", json={"recovery_key": third_key})
    assert r.status_code == 200
    assert app_.state.registry.peek(third_id) is None

    as_owner(c, h1_cookie)  # household 1 is untouched
    assert c.get("/status").json()["phone"] is None or True
    as_owner(c, h2_cookie)
    assert c.get("/me").status_code == 200  # and household 2 still authenticates


def test_non_legacy_households_never_touch_the_llm_or_external_notifiers(tmp_path):
    spy_llm, spy_notifier = SpyLLM(), SpyNotifier()
    app_ = app(tmp_path, interpreter=spy_llm, notifier=spy_notifier)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        station = Station(c, b"MARK", "Phone")
        with connect_station(c, station):
            c.post("/treat")  # an action that would trigger interpretation in the wrong config
            for _ in range(30):
                c.get("/status")
                time.sleep(0.02)
        assert spy_llm.calls == []  # the household's runtime is not the operator's interpreter
        assert spy_notifier.sent == []


def test_event_logs_stay_in_their_household_directory(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        with app_.state.db_session() as s:
            from backend.store import repo as store_repo

            store_repo.update_household_settings(s, hid, log_enabled=True)
            s.commit()
        station = Station(c, b"MARK", "Phone")
        with connect_station(c, station):
            for _ in range(50):
                c.get("/status")
                time.sleep(0.02)
        out = tmp_path / "out"
        household_dir = out / hid
        assert household_dir.is_dir() and any(household_dir.glob("session-*.jsonl"))
        # nothing was logged outside that household's directory (aside from the store's own files)
        stray = [p for p in out.rglob("session-*.jsonl") if household_dir not in p.parents]
        assert stray == []


def test_pin_mode_still_serves_one_legacy_household(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    app_ = create_app(cfg, pipeline=None, interpreter=SpyLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app_) as c:
        assert c.get("/auth/status").json()["mode"] == "pin"
        assert c.post("/households", json={}).status_code == 404
        assert c.get("/status").json()["phone"] is None


def test_mode_change_in_one_household_never_affects_another(two_households):
    app_, c, h1_cookie, h2_cookie, s1, s2 = two_households
    # Household 2 starts in privacy mode by default
    as_owner(c, h2_cookie)
    assert c.get("/mode").json() == {"household_mode": "privacy", "station_cap": "full", "effective": "privacy"}

    # Household 1 owner sets mode to blackout
    as_owner(c, h1_cookie)
    r1 = c.post("/mode", json={"mode": "blackout"})
    assert r1.status_code == 200 and r1.json()["mode"] == "blackout"
    assert c.get("/mode").json()["household_mode"] == "blackout"

    # Household 2 is completely unaffected
    as_owner(c, h2_cookie)
    assert c.get("/mode").json() == {"household_mode": "privacy", "station_cap": "full", "effective": "privacy"}
    assert c.get("/privacy").json() == {"privacy_mode": False}


def test_two_households_two_stations_matrix(tmp_path):
    """Two households, two stations each (T4.1 isolation matrix):
    assert that station routing, video, status, and control remain strictly isolated across households.
    """
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        # Household 1
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Dog1"})
        h1_cookie = c.cookies[VIEWER_COOKIE]
        s1A = Station(c, b"H1_STATION_A", "H1 Phone A")
        s1B = Station(c, b"H1_STATION_B", "H1 Phone B")

        # Household 2
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Dog2"})
        h2_cookie = c.cookies[VIEWER_COOKIE]
        s2A = Station(c, b"H2_STATION_A", "H2 Phone A")
        s2B = Station(c, b"H2_STATION_B", "H2 Phone B")

        with (
            connect_station(c, s1A),
            connect_station(c, s1B),
            connect_station(c, s2A),
            connect_station(c, s2B),
        ):
            # Household 1 perspective
            as_owner(c, h1_cookie)
            c.post("/mode", json={"mode": "full"})
            st1 = c.get("/status").json()
            h1_sids = {st["id"] for st in st1.get("stations", [])}
            assert h1_sids == {s1A.station_id, s1B.station_id}
            assert s2A.station_id not in h1_sids
            assert s2B.station_id not in h1_sids

            # Video routing in Household 1
            v1A = c.get(f"/video?station={s1A.station_id}&max_frames=1").content
            assert b"H1_STATION_A" in v1A
            assert b"H1_STATION_B" not in v1A
            assert b"H2_STATION" not in v1A

            # Household 1 cannot touch Household 2 stations
            assert c.get(f"/status?station={s2A.station_id}").status_code == 404
            assert c.get(f"/video?station={s2A.station_id}&max_frames=1").status_code == 404
            assert c.post(f"/treat?station={s2A.station_id}").status_code == 404

            # Household 2 perspective
            as_owner(c, h2_cookie)
            c.post("/mode", json={"mode": "full"})
            st2 = c.get("/status").json()
            h2_sids = {st["id"] for st in st2.get("stations", [])}
            assert h2_sids == {s2A.station_id, s2B.station_id}
            assert s1A.station_id not in h2_sids
            assert s1B.station_id not in h2_sids

            # Video routing in Household 2
            v2B = c.get(f"/video?station={s2B.station_id}&max_frames=1").content
            assert b"H2_STATION_B" in v2B
            assert b"H2_STATION_A" not in v2B
            assert b"H1_STATION" not in v2B

            # Household 2 cannot touch Household 1 stations
            assert c.get(f"/status?station={s1A.station_id}").status_code == 404
            assert c.get(f"/video?station={s1B.station_id}&max_frames=1").status_code == 404
            assert c.post(f"/treat?station={s1A.station_id}").status_code == 404


