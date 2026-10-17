"""Station pairing v2, backend (T2.4): a code buys a station row, a signature buys the socket.

The test helper generates a real ECDSA P-256 key with `cryptography` — the same algorithm the
Android Keystore will use — pairs it, then signs the server's challenge exactly like the app will.
"""

import base64
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config

AUTH_MSG_PREFIX = b"btb-auth-v1|"


class NoLLM:
    enabled = False
    last_call = None


class StationKey:
    """A stand-in for the app's AndroidKeyStore signer."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())

    def spki_der(self) -> bytes:
        return self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)

    def spki_b64(self) -> str:
        return base64.b64encode(self.spki_der()).decode()

    def sign(self, message: bytes) -> str:
        return base64.b64encode(self.key.sign(message, ec.ECDSA(hashes.SHA256()))).decode()


def hello_proto2(station_id: str, device: str = "Pixel 8") -> dict:
    return {"type": "hello", "proto": 2, "station_id": station_id, "device": device,
            "phone_time": time.time(), "models": {}, "profile": "mobile"}


def auth_message(nonce_b64: str, station_id: str, device: str) -> bytes:
    return AUTH_MSG_PREFIX + base64.b64decode(nonce_b64) + b"|" + station_id.encode() + b"|" + device.encode()


def app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["rate_limits"] = {"enabled": True}
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def paired_owner(c):
    """Create a household (sets the cookie) and return its pairing code for a station."""
    c.post("/households", json={"dog_name": "Rex"})
    r = c.post("/households/me/pairing", json={"kind": "station"})
    assert r.status_code == 200
    return r.json()["code"]


def test_pairing_creates_the_station_and_the_qr_carries_the_code(tmp_path):
    with TestClient(app(tmp_path)) as c:
        code = paired_owner(c)
        r = c.post("/pair", json={"code": code, "pubkey_spki_b64": StationKey().spki_b64(), "device_name": "Pixel 8"})
        assert r.status_code == 200
        body = r.json()
        assert body["station_id"] and body["household_id"]


def test_pairing_publishes_status_to_websocket_subscribers(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        code = paired_owner(c)
        with c.websocket_connect("/ws", cookies={VIEWER_COOKIE: c.cookies[VIEWER_COOKIE]}) as dash:
            init = dash.receive_json()
            assert init["type"] == "status"
            assert "stations" in init["data"]

            key = StationKey()
            r = c.post("/pair", json={"code": code, "pubkey_spki_b64": key.spki_b64(), "device_name": "Pixel 8"})
            assert r.status_code == 200
            station_id = r.json()["station_id"]

            msg = dash.receive_json()
            assert msg["type"] == "status"
            assert msg["data"].get("paired_station") == {"id": station_id, "name": "Pixel 8"}
            assert any(s["id"] == station_id for s in msg["data"].get("stations", []))


def test_qr_payload_shape(tmp_path):
    with TestClient(app(tmp_path)) as c:
        code = paired_owner(c)
        qr = c.post("/households/me/pairing", json={"kind": "station"}).json()["qr"]
        assert qr["v"] == 2 and qr["code"] != code  # a fresh code per request
        assert qr["url"].endswith("/ingest-events") and qr["pair_url"].endswith("/pair")


def test_pairing_code_is_single_use_and_kind_matters(tmp_path):
    with TestClient(app(tmp_path)) as c:
        code = paired_owner(c)
        key = StationKey()
        first = c.post("/pair", json={"code": code, "pubkey_spki_b64": key.spki_b64(), "device_name": "Pixel 8"})
        assert first.status_code == 200
        again = c.post("/pair", json={"code": code, "pubkey_spki_b64": key.spki_b64(), "device_name": "Pixel 8"})
        assert again.status_code == 400 and again.json() == {"error": "invalid_or_expired_code"}


def test_pairing_rejects_malformed_pubkey_and_bad_name(tmp_path):
    with TestClient(app(tmp_path)) as c:
        code = paired_owner(c)
        bad = c.post("/pair", json={"code": code, "pubkey_spki_b64": "not-base64!!", "device_name": "x"})
        assert bad.status_code == 400
        code2 = paired_owner(c)
        rsa_key = c.post("/pair", json={"code": code2, "pubkey_spki_b64": base64.b64encode(b"\x30\x82\x01\x00nonsense").decode(),
                                        "device_name": "x"})
        assert rsa_key.status_code == 400  # not a P-256 SPKI key
        code3 = paired_owner(c)
        assert c.post("/pair", json={"code": code3, "pubkey_spki_b64": StationKey().spki_b64(), "device_name": ""}).status_code == 400


def test_happy_path_challenge_then_connect(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        code = paired_owner(c)
        key = StationKey()
        station_id = c.post("/pair", json={"code": code, "pubkey_spki_b64": key.spki_b64(),
                                           "device_name": "Pixel 8"}).json()["station_id"]
        with c.websocket_connect("/ingest-events") as ws:
            import json

            ws.send_text(json.dumps(hello_proto2(station_id)))
            challenge = ws.receive_json()
            assert challenge["type"] == "challenge" and challenge["nonce"]
            ws.send_text(json.dumps({"type": "auth", "sig": key.sign(auth_message(challenge["nonce"], station_id, "Pixel 8"))}))
            ack = ws.receive_json()
            assert ack["type"] == "hello_ack"
            lr = app_.state.registry.peek(c.get("/me").json()["household_id"])
            assert lr is not None and lr.runtime.phone is not None


def test_wrong_signature_and_wrong_station_close_4401(tmp_path):
    with TestClient(app(tmp_path)) as c:
        code = paired_owner(c)
        station_id = c.post("/pair", json={"code": code, "pubkey_spki_b64": StationKey().spki_b64(),
                                           "device_name": "Pixel 8"}).json()["station_id"]
        other = StationKey()
        import json

        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(json.dumps(hello_proto2(station_id)))
            nonce = ws.receive_json()["nonce"]
            ws.send_text(json.dumps({"type": "auth", "sig": other.sign(auth_message(nonce, station_id, "Pixel 8"))}))
            assert ws.receive().get("code") == 4401

        # a signature for a different station id must not pass either
        code2 = paired_owner(c)
        key = StationKey()
        sid2 = c.post("/pair", json={"code": code2, "pubkey_spki_b64": key.spki_b64(),
                                     "device_name": "Pixel 8"}).json()["station_id"]
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(json.dumps(hello_proto2(sid2)))
            nonce = ws.receive_json()["nonce"]
            ws.send_text(json.dumps({"type": "auth", "sig": key.sign(auth_message(nonce, "not-this-station", "Pixel 8"))}))
            assert ws.receive().get("code") == 4401


def test_proto1_in_households_mode_is_unauthorized(tmp_path):
    import json

    with TestClient(app(tmp_path)) as c:
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(json.dumps({"type": "hello", "proto": 1, "device": "Legacy", "phone_time": time.time()}))
            assert ws.receive().get("code") == 4401


def test_unknown_station_id_is_unauthorized(tmp_path):
    import json

    with TestClient(app(tmp_path)) as c:
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(json.dumps(hello_proto2("never-paired")))
            assert ws.receive().get("code") == 4401


def test_revoked_station_cannot_connect_and_live_sockets_get_4410(tmp_path):
    import json

    app_ = app(tmp_path)
    with TestClient(app_) as c:
        code = paired_owner(c)
        key = StationKey()
        station_id = c.post("/pair", json={"code": code, "pubkey_spki_b64": key.spki_b64(),
                                           "device_name": "Pixel 8"}).json()["station_id"]
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(json.dumps(hello_proto2(station_id)))
            nonce = ws.receive_json()["nonce"]
            ws.send_text(json.dumps({"type": "auth", "sig": key.sign(auth_message(nonce, station_id, "Pixel 8"))}))
            assert ws.receive_json()["type"] == "hello_ack"
            hid = c.get("/me").json()["household_id"]
            lr = app_.state.registry.peek(hid)
            assert lr.runtime.phone is not None
            # the owner revokes the station from the dashboard
            r = c.request("DELETE", f"/stations/{station_id}")
            assert r.status_code == 200
            assert ws.receive().get("code") == 4410  # the socket is closed with the revoked code
            assert lr.runtime.phone is None


def test_delete_station_of_another_household_is_404(tmp_path):
    with TestClient(app(tmp_path)) as c:
        code = paired_owner(c)
        sid = c.post("/pair", json={"code": code, "pubkey_spki_b64": StationKey().spki_b64(),
                                    "device_name": "Pixel 8"}).json()["station_id"]
        c.cookies.clear()
        c.post("/households", json={})  # a second household
        assert c.request("DELETE", f"/stations/{sid}").status_code == 404


def test_pair_failure_rate_limit(tmp_path):
    app_ = app(tmp_path)
    with TestClient(app_) as c:
        code = paired_owner(c)
        for _ in range(10):
            c.post("/pair", json={"code": "0102030405060708", "pubkey_spki_b64": StationKey().spki_b64(),
                                  "device_name": "x"})
        assert c.post("/pair", json={"code": code, "pubkey_spki_b64": StationKey().spki_b64(),
                                     "device_name": "x"}).status_code == 429


def test_a_binary_frame_instead_of_the_auth_reply_closes_with_4401(tmp_path):
    """The auth step used receive_text(), which raises on a binary frame: that must be a clean 4401."""
    import json

    with TestClient(app(tmp_path)) as c:
        code = paired_owner(c)
        station_id = c.post("/pair", json={"code": code, "pubkey_spki_b64": StationKey().spki_b64(),
                                           "device_name": "Pixel 8"}).json()["station_id"]
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(json.dumps(hello_proto2(station_id)))
            assert ws.receive_json()["type"] == "challenge"
            ws.send_bytes(b"\x01not-json")
            assert ws.receive().get("code") == 4401
