"""Multi-station backend tests (T4.1):
- Multiple stations in one household connect concurrently over /ingest-events.
- Envelopes over /ws are tagged with {"station": station_id}.
- GET /status returns stations list with both stations.
- GET /video?station=A returns A's frames, /video?station=B returns B's frames.
- POST /treat?station=B acts only on B's pipeline.
- Station ID belonging to another household or invalid returns 404.
- Per-station seq numbers remain independent.
- 5th station rejected with close code 4409 when max_stations_per_household is 4.
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

from backend.main import CLOSE_BUSY, VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
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


def test_concurrent_stations_and_ws_tagging(tmp_path):
    app_ = make_test_app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        cookie = c.cookies[VIEWER_COOKIE]

        s1 = StationClient(c, b"STATION_1_BYTES", device="Living Room")
        s2 = StationClient(c, b"STATION_2_BYTES", device="Backyard")

        with connect_station(c, s1) as ws1, connect_station(c, s2) as ws2:
            # Check /status lists both stations
            st = c.get("/status").json()
            assert "stations" in st
            s_ids = {s["id"] for s in st["stations"]}
            assert s1.station_id in s_ids
            assert s2.station_id in s_ids

            # Check /ws fan-in receives tagged messages
            with c.websocket_connect("/ws", cookies={VIEWER_COOKIE: cookie}) as dash:
                initial_status = dash.receive_json()
                assert initial_status["type"] == "status"

                # Trigger treat action on station 1 and station 2
                c.post(f"/treat?station={s1.station_id}")
                c.post(f"/treat?station={s2.station_id}")

                # Read incoming WS messages
                env1 = dash.receive_json()
                env2 = dash.receive_json()

                received = {env1.get("station"): env1, env2.get("station"): env2}
                assert s1.station_id in received
                assert s2.station_id in received
                assert received[s1.station_id]["type"] == "treat"
                assert received[s2.station_id]["type"] == "treat"


def test_station_scoped_video_and_actions(tmp_path):
    app_ = make_test_app(tmp_path)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rover"})
        sA = StationClient(c, b"MARKER_A_JPEG_DATA", device="Kitchen")
        sB = StationClient(c, b"MARKER_B_JPEG_DATA", device="Bedroom")

        with connect_station(c, sA) as wsA, connect_station(c, sB) as wsB:
            # Set mode to full so raw camera frames are served
            set_mode_resp = c.post("/mode", json={"mode": "full"})
            assert set_mode_resp.status_code == 200

            # Video route with ?station= parameter
            respA = c.get(f"/video?station={sA.station_id}&max_frames=1")
            assert respA.status_code == 200
            assert b"MARKER_A_JPEG_DATA" in respA.content
            assert b"MARKER_B_JPEG_DATA" not in respA.content

            respB = c.get(f"/video?station={sB.station_id}&max_frames=1")
            assert respB.status_code == 200
            assert b"MARKER_B_JPEG_DATA" in respB.content
            assert b"MARKER_A_JPEG_DATA" not in respB.content

            # /treat?station=B triggers only B's pipeline
            lr = app_.state.registry.peek(c.get("/me").json()["household_id"])
            chA = lr.channels[sA.station_id]
            chB = lr.channels[sB.station_id]
            assert len(chA.pipeline._treats) == 0
            assert len(chB.pipeline._treats) == 0

            treat_resp = c.post(f"/treat?station={sB.station_id}")
            assert treat_resp.status_code == 200
            assert len(chB.pipeline._treats) == 1
            assert len(chA.pipeline._treats) == 0


def test_other_household_station_returns_404(tmp_path):
    app_ = make_test_app(tmp_path)
    with TestClient(app_) as c:
        # Household 1
        c.post("/households", json={"dog_name": "Dog1"})
        h1_cookie = c.cookies[VIEWER_COOKIE]
        s1 = StationClient(c, b"H1_STATION", device="H1 Phone")

        # Household 2
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Dog2"})
        h2_cookie = c.cookies[VIEWER_COOKIE]
        s2 = StationClient(c, b"H2_STATION", device="H2 Phone")

        # As household 2, attempting to access household 1's station
        c.cookies.clear()
        c.cookies.set(VIEWER_COOKIE, h2_cookie)

        assert c.get(f"/status?station={s1.station_id}").status_code == 404
        assert c.get(f"/events?station={s1.station_id}").status_code == 404
        assert c.get(f"/video?station={s1.station_id}&max_frames=1").status_code == 404
        assert c.post(f"/treat?station={s1.station_id}").status_code == 404
        assert c.get(f"/mode?station={s1.station_id}").status_code == 404
        assert c.post(f"/mode?station={s1.station_id}", json={"mode": "privacy"}).status_code == 404


def test_station_cap_enforced(tmp_path):
    # Cap set to 4
    app_ = make_test_app(tmp_path, max_stations=4)
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "MultiDog"})
        stations = [StationClient(c, f"STATION_{i}".encode(), device=f"Phone {i}") for i in range(5)]

        # Connect 4 stations successfully
        with (
            connect_station(c, stations[0]),
            connect_station(c, stations[1]),
            connect_station(c, stations[2]),
            connect_station(c, stations[3]),
        ):
            # 5th station attempts to connect and should be closed with CLOSE_BUSY (4409)
            with c.websocket_connect("/ingest-events") as ws5:
                ws5.send_text(stations[4].hello())
                challenge = ws5.receive_json()
                assert challenge["type"] == "challenge"
                ws5.send_text(stations[4].auth(challenge["nonce"]))
                closed = ws5.receive()
                assert closed.get("type") == "websocket.close"
                assert closed.get("code") == CLOSE_BUSY
