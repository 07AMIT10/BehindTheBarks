"""GET /audio (T3.9, A3.4): a viewer receives `audio_hello`, then the phone's frames byte for byte minus the
0x02 byte. Pin mode and households mode. Refusals and mode/kick behaviour are in test_audio_isolation.py.
"""

import base64
import contextlib
import json
import time

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



def pin_app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["pipeline"] = "remote"
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def pin_phone_hello() -> str:
    return json.dumps({"type": "hello", "proto": 1, "device": "Pixel", "phone_time": time.time(),
                       "models": {}, "profile": "mobile"})


def adpcm(seq: int) -> bytes:
    return ac.pack_frame(ac.CODEC_IMA_ADPCM, seq, 1000 + seq, ac.encode_block([seq % 7] * ac.CHUNK_SAMPLES))


def pcm(seq: int) -> bytes:
    return ac.pack_frame(ac.CODEC_PCM16, seq, 2000 + seq, bytes(ac.PCM16_PAYLOAD_BYTES))


def test_pin_mode_viewer_gets_hello_then_the_phone_frames_without_the_magic_byte(tmp_path):
    app_ = pin_app(tmp_path)
    with TestClient(app_) as c:
        assert c.post("/mode", json={"mode": "full"}).status_code == 200
        with c.websocket_connect("/ingest-events") as phone:
            phone.send_text(pin_phone_hello())
            assert phone.receive_json()["type"] == "hello_ack"
            with c.websocket_connect("/audio") as viewer:
                hello = viewer.receive_json()
                assert hello == {"type": "audio_hello", "codec": 1, "rate": 16000, "chunk_ms": 100,
                                 "station": "legacy"}
                f1 = adpcm(1)
                phone.send_bytes(f1)
                assert viewer.receive_bytes() == f1[1:]
                f2 = pcm(2)  # codec change mid-stream: a fresh hello, then the frame
                phone.send_bytes(f2)
                assert viewer.receive_json() == {"type": "audio_hello", "codec": 0, "rate": 16000,
                                                 "chunk_ms": 100, "station": "legacy"}
                assert viewer.receive_bytes() == f2[1:]


def test_households_station_audio_reaches_the_owner_exactly(tmp_path):
    app_ = make_test_app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        cookie = c.cookies[VIEWER_COOKIE]
        st = StationClient(c, b"AUDIO_STATION", device="Kitchen")
        assert c.post("/mode", json={"mode": "full"}).status_code == 200
        with connect_station(c, st) as phone:
            with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: cookie}) as viewer:
                hello = viewer.receive_json()
                assert hello["type"] == "audio_hello"
                assert hello["station"] == st.station_id
                assert hello["codec"] == 1
                frame = adpcm(7)
                phone.send_bytes(frame)
                assert viewer.receive_bytes() == frame[1:]


def test_station_audio_is_not_relayed_without_a_listener_and_is_counted(tmp_path):
    app_ = make_test_app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        st = StationClient(c, b"NO_LISTENER", device="Hall")
        assert c.post("/mode", json={"mode": "full"}).status_code == 200
        with connect_station(c, st) as phone:
            phone.send_bytes(adpcm(1))
            pipe = app_.state.registry.peek(c.get("/me").json()["household_id"]).channels[st.station_id].pipeline
            status = pipe.status()
            assert status["audio_dropped_policy"] == 1
            assert status["audio_frames"] == 0
            assert status["audio_clients"] == 0
            assert status["audio_dropped_invalid"] == 0
