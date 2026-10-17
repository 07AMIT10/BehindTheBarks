"""Audio listener demand drives the policy downlink (T3.9, A3.3).

`viewers.audio` counts the /audio listeners of a station; `audio` is true exactly while the station is in
Full mode and at least one listener is connected. A listener that disappears (clean close or a dropped
socket) releases both, so no ghost listener keeps the phone's microphone on.
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
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web import audio_codec as ac
from backend.web.ingest_events import KIND_PREVIEW
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


class NoLLM:
    enabled = False
    last_call = None


def make_test_app(tmp_path, max_stations: int = 4):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["max_stations_per_household"] = max_stations
    cfg["web"]["video"]["idle_timeout_s"] = 0.5
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


class StationClient:
    def __init__(self, c: TestClient, marker: bytes, device: str = "Pixel 8"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.marker = marker
        self.device = device
        code = c.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        spki = base64.b64encode(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        ).decode()
        r = c.post("/pair", json={"code": code, "pubkey_spki_b64": spki, "device_name": device}).json()
        self.station_id = r["station_id"]
        self.household_id = r["household_id"]

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

    def marker_jpeg(self) -> bytes:
        return b"\xff\xd8" + self.marker + b"\xff\xd9"

    def frame(self, dog_detected: bool = True) -> str:
        return json.dumps({
            "type": "frame",
            "data": {
                "ts": time.time(),
                "source": "live",
                "dog_detected": dog_detected,
                "bbox": [1.0, 2.0, 30.0, 40.0],
                "bbox_conf": 0.9,
                "body_keypoints": {"tail_base": [10.0, 20.0, 0.9]},
                "face_landmarks": None,
                "features": {
                    "tail_height": 0.4,
                    "tail_wag_hz": 2.1,
                    "ear_position": "up",
                    "mouth_open": 0.6,
                    "body_lowering": 0.1,
                    "motion_energy": 0.7,
                    "in_feeding_zone": True,
                },
            },
        })


@contextlib.contextmanager
def connect_station(c: TestClient, station: StationClient):
    with c.websocket_connect("/ingest-events") as ws:
        ws.send_text(station.hello())
        challenge = ws.receive_json()
        assert challenge["type"] == "challenge"
        ws.send_text(station.auth(challenge["nonce"]))
        ack = ws.receive_json()
        assert ack["type"] == "hello_ack"
        ws.send_bytes(bytes([KIND_PREVIEW]) + station.marker_jpeg())
        ws.send_text(station.frame())
        yield ws




def adpcm(seq: int) -> bytes:
    return ac.pack_frame(ac.CODEC_IMA_ADPCM, seq, 1000 + seq, ac.encode_block([seq % 7] * ac.CHUNK_SAMPLES))



class Downlinks:
    def __init__(self):
        self.sent = []

    def post(self, msg):
        self.sent.append(msg)
        return True

    def policies(self):
        return [m["data"] for m in self.sent if m.get("type") == "policy"]


@pytest.fixture()
def station_app(tmp_path):
    """A household with one connected station in Full mode. The channel exists only while the station's
    socket is open, so the phone stays connected for the whole test."""
    app_ = make_test_app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        cookie = c.cookies[VIEWER_COOKIE]
        st = StationClient(c, b"POLICY_STATION", device="Den")
        hid = c.get("/me").json()["household_id"]
        with connect_station(c, st):
            assert c.post("/mode", json={"mode": "full"}).status_code == 200
            ch = app_.state.registry.peek(hid).channels[st.station_id]
            ch.tracker.debounce_s = 0.0  # every change pushes at once, so the test reads each downlink
            rec = Downlinks()
            ch.pipeline._post = rec.post
            yield app_, c, cookie, st, ch, rec


def test_listeners_count_separately_and_drive_the_audio_flag(station_app):
    app_, c, cookie, st, ch, rec = station_app
    assert ch.tracker.viewer_counts == {"video": 0, "audio": 0}
    ch.tracker.enter_audio("a-1", viewer_id="v-1", label="Chrome")
    assert ch.tracker.viewer_counts == {"video": 0, "audio": 1}
    assert rec.policies()[-1]["audio"] is True
    assert rec.policies()[-1]["viewers"] == {"video": 0, "audio": 1}

    ch.tracker.enter_audio("a-2", viewer_id="v-2", label="Firefox")
    assert rec.policies()[-1]["viewers"]["audio"] == 2

    ch.tracker.leave_audio("a-1")
    assert rec.policies()[-1]["viewers"]["audio"] == 1
    assert rec.policies()[-1]["audio"] is True

    ch.tracker.leave_audio("a-2")
    assert rec.policies()[-1]["viewers"] == {"video": 0, "audio": 0}
    assert rec.policies()[-1]["audio"] is False


def test_leaving_twice_is_harmless(station_app):
    _, _, _, _, ch, rec = station_app
    ch.tracker.enter_audio("a-1", viewer_id="v-1")
    ch.tracker.leave_audio("a-1")
    ch.tracker.leave_audio("a-1")
    assert ch.tracker.viewer_counts["audio"] == 0


def test_audio_flag_is_false_outside_full_mode_even_with_listeners(station_app):
    _, c, _, _, ch, rec = station_app
    ch.tracker.enter_audio("a-1", viewer_id="v-1")
    assert rec.policies()[-1]["audio"] is True
    c.post("/mode", json={"mode": "limited"})  # the owner lowers the household mode
    assert rec.policies()[-1]["mode"] == "limited"
    assert rec.policies()[-1]["audio"] is False
    assert rec.policies()[-1]["viewers"]["audio"] == 1  # the listener is still counted, just not served
    c.post("/mode", json={"mode": "full"})
    assert rec.policies()[-1]["audio"] is True


def test_a_station_cap_drop_pushes_the_audio_flag_off(station_app):
    _, _, _, _, ch, rec = station_app
    ch.tracker.enter_audio("a-1", viewer_id="v-1")
    assert rec.policies()[-1]["audio"] is True
    ch.pipeline.set_station_cap("privacy")  # the phone itself lowered its cap
    assert rec.policies()[-1]["audio"] is False


def test_the_ws_listener_disconnect_releases_the_listener(station_app):
    """A listener whose socket closes (cleanly or not) must not stay counted: the /audio handler's
    finally releases it, which is what keeps a ghost listener from holding the phone's mic open."""
    _, c, cookie, st, ch, rec = station_app
    with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: cookie}) as viewer:
        viewer.receive_json()  # audio_hello: the listener is registered before this
        assert ch.tracker.viewer_counts["audio"] == 1
        assert rec.policies()[-1]["audio"] is True
    # the with-block closed the socket; the handler has finished by now
    assert ch.tracker.viewer_counts["audio"] == 0
    assert rec.policies()[-1]["viewers"]["audio"] == 0
    assert rec.policies()[-1]["audio"] is False
    assert ch.audio.client_count == 0


def test_a_client_that_never_reads_leaves_no_listener_behind(station_app):
    """Opening /audio and dropping immediately (before any frame) must not leave a listener either."""
    _, c, cookie, st, ch, rec = station_app
    with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: cookie}):
        pass
    assert ch.tracker.viewer_counts["audio"] == 0
    assert ch.audio.client_count == 0
