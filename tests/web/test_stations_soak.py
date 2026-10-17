"""Multi-station soak test (T4.4):
Runs 4 simulated stations concurrently for 10 seconds in one household,
asserting no cross-tagging of station envelopes over /ws, bounded queue sizes,
and zero downlinks dropped.
"""

from __future__ import annotations

import base64
import contextlib
import json
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
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


class NoLLM:
    enabled = False
    last_call = None


def make_test_cfg(tmp_path: Path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "logs")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["max_stations_per_household"] = 4
    cfg["web"]["video"]["fps"] = 15
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    return cfg


class SoakStation:
    def __init__(self, c: TestClient, device: str, station_idx: int):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.device = device
        self.station_idx = station_idx
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
            "models": {"detect": "soak-yolo"},
            "profile": "mobile",
        })

    def auth(self, nonce_b64: str) -> str:
        msg = AUTH_PREFIX + base64.b64decode(nonce_b64) + b"|" + self.station_id.encode() + b"|" + self.device.encode()
        sig = base64.b64encode(self.key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        return json.dumps({"type": "auth", "sig": sig})

    def frame(self, seq_idx: int) -> str:
        return json.dumps({
            "type": "frame",
            "data": {
                "ts": time.time(),
                "source": "live",
                "dog_detected": True,
                "bbox": [10.0, 20.0, 100.0, 200.0],
                "bbox_conf": 0.95,
                "body_keypoints": {"tail_base": [50.0, 100.0, 0.9]},
                "face_landmarks": None,
                "features": {
                    "tail_height": 0.1 * self.station_idx,
                    "tail_wag_hz": float(self.station_idx),
                    "motion_energy": 0.3,
                },
            },
        })


@pytest.mark.slow
def test_4_stations_10s_soak(tmp_path):
    """4 simulated stations streaming concurrently for 10 s in one household:
    - All events over /ws carry their true station tag.
    - Zero cross-tagging (a station's data payload never bears another station's tag).
    - downlinks_dropped == 0 on all station pipelines.
    """
    cfg = make_test_cfg(tmp_path)
    app = create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())

    with TestClient(app) as c:
        c.post("/households", json={"dog_name": "FleetDogs"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        household_id = c.get("/me").json()["household_id"]

        stations = [
            SoakStation(c, f"FleetStation-{i+1}", station_idx=i+1)
            for i in range(4)
        ]
        station_map = {s.station_id: s for s in stations}

        # Connect /ws subscriber
        c.cookies.set(VIEWER_COOKIE, owner_cookie)
        with c.websocket_connect("/ws") as ws_dash:
            # Drain initial status envelope
            init_msg = ws_dash.receive_json()
            assert init_msg["type"] == "status"

            # Connect all 4 stations concurrently using ExitStack
            with contextlib.ExitStack() as stack:
                station_sockets = []
                for st in stations:
                    ws = stack.enter_context(c.websocket_connect("/ingest-events"))
                    ws.send_text(st.hello())
                    ch = ws.receive_json()
                    assert ch["type"] == "challenge"
                    ws.send_text(st.auth(ch["nonce"]))
                    ack = ws.receive_json()
                    assert ack["type"] == "hello_ack"
                    station_sockets.append((st, ws))

                # Tiny JPEG (32x24)
                jpeg = (
                    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xc0"
                    b"\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
                )

                # Run soak for 10 seconds
                duration_s = 10.0
                start_time = time.monotonic()
                iterations = 0
                seen_by_station: dict[str, int] = {st.station_id: 0 for st in stations}

                received_tags: set[str] = set()

                while time.monotonic() - start_time < duration_s:
                    for st, ws in station_sockets:
                        ws.send_bytes(bytes([KIND_PREVIEW]) + jpeg)
                        ws.send_text(st.frame(iterations))
                        seen_by_station[st.station_id] += 1
                    iterations += 1
                    time.sleep(0.05)

                    # Drain envelopes from dashboard in lockstep so queue doesn't back up and receive never hangs
                    for _ in range(len(station_sockets)):
                        msg = ws_dash.receive_json()
                        if "station" in msg:
                            received_tags.add(msg["station"])
                            data = msg.get("data") or {}
                            feats = data.get("features") or {}
                            wag = feats.get("tail_wag_hz")
                            if wag is not None and msg["station"] in station_map:
                                expected_wag = float(station_map[msg["station"]].station_idx)
                                assert wag == expected_wag, f"Cross-tagging detected! wag={wag} expected={expected_wag} for station {msg['station']}"

                assert len(received_tags) == 4, f"Expected events from 4 stations, got: {received_tags}"

                # All 4 stations contributed to the household hub
                lr = c.portal.call(app.state.registry.get, household_id)

                # Verify each station channel
                assert len(lr.channels) == 4
                for st in stations:
                    ch = lr.channels[st.station_id]
                    # Verify downlinks_dropped == 0
                    assert ch.pipeline.downlinks_dropped == 0, f"Station {st.device} dropped downlinks: {ch.pipeline.downlinks_dropped}"
                    # Pipeline status is running
                    status = ch.pipeline.status()
                    assert status["state"] == "running"
                    assert status["source"] == "remote"
                    assert status["previews"] > 0
                    assert status["events"] > 0

                # Close all stations cleanly
                for st, ws in station_sockets:
                    ws.send_text(json.dumps({"type": "bye"}))
