"""Phone-initiated `kick_viewers` uplink (T3.4 station sovereignty).

The phone must be able to reclaim its camera with one uplink message: on `{type:
kick_viewers}` (backend/web/ingest_events.py `_kick_viewers`) the station channel's
tracker kicks every current viewer (the open /audio listener is closed with 4411) and
imposes the same 60 s cooldown the owner-side POST /viewers/disconnect-all uses, so
new listeners are refused until it lapses (asserted here with an injected fake clock,
the pattern from test_antilurking.py).

In a one-station household everything routes through that channel's tracker: the
registry pops the placeholder "default" channel and makes the phone's channel the
household default, so /video and /audio admitted without an explicit station land on
it. The kick cooldown on the tracker instance is therefore exactly what a subsequent
403 "Viewers disconnected..." on /video and a 4411 audio refusal both read, and the
owner-dashboard kick and the phone uplink kick arm that one shared tracker.

Socket lifetimes stay inside `with` blocks throughout (the test-suite's own
convention): a TestClient session whose sockets are never context-exited keeps its
portal threads alive and wedges the client teardown.
"""

import base64
import json
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.ingest_events import KIND_PREVIEW
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"
FAKE_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"


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


def _owner(c: TestClient, cookie: str) -> None:
    c.cookies.clear()
    c.cookies.set(VIEWER_COOKIE, cookie)


def _viewer_cookie(c: TestClient, owner_cookie: str) -> str:
    """Pair a viewer into the household owned by owner_cookie; return its session cookie."""
    _owner(c, owner_cookie)
    code = c.post("/households/me/pairing", json={"kind": "viewer", "label": "Phone"}).json()["code"]
    c.cookies.clear()
    assert c.post("/join", json={"code": code}).status_code == 200
    return c.cookies[VIEWER_COOKIE]


def _audio_close_code(c: TestClient, cookies: dict) -> int:
    """Open /audio, expect a refusal, and return its close code."""
    c.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as err:
        with c.websocket_connect("/audio", cookies=cookies) as ws:
            ws.receive_json()
    return err.value.code


def _config(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 50
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    return create_app(cfg, pipeline=None, interpreter=None, notifier=DashboardOnlyNotifier())


def test_phone_kick_viewers_uplink_disconnects_listeners_and_cools_down(tmp_path):
    with TestClient(_config(tmp_path)) as c:
        # Owner household, one viewer, a paired station; the phone streams over /ingest-events.
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Rex"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        station = Station(c, b"SOMARKERBYTES", "PhoneOne")
        viewer_cookie = _viewer_cookie(c, owner_cookie)

        with c.websocket_connect("/ingest-events") as phone:
            phone.send_text(station.hello())
            challenge = phone.receive_json()
            assert challenge["type"] == "challenge"
            phone.send_text(station.auth(challenge["nonce"]))
            assert phone.receive_json()["type"] == "hello_ack"

            _owner(c, owner_cookie)
            assert c.post("/mode", json={"mode": "full"}).status_code == 200  # audio needs the full mode
            phone.send_bytes(bytes([KIND_PREVIEW]) + FAKE_JPEG)

            # A viewer listens to the station's audio before the phone claims its camera back.
            c.cookies.clear()
            with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: viewer_cookie}) as audio_ws:
                assert audio_ws.receive_json()["type"] == "audio_hello"

                # Precise-but-boring cooldown testing: the fake clock is injected into the
                # station channel's tracker BEFORE the kick lands, so `_kicked_until` is
                # stamped against it (test_antilurking's pattern, through the phone socket).
                lr = c.portal.call(c.app.state.registry.get, station.household_id)
                ch = c.portal.call(lr.channels.get, station.station_id)
                assert ch is not None, "the streaming phone must own a station channel"
                now = [time.time()]
                c.portal.call(setattr, ch.tracker, "clock", lambda: now[0])

                # The phone itself asks for every remote viewer off the wire.
                phone.send_text(json.dumps({"type": "kick_viewers"}))

                # 1. The listener that was already open is closed with 4411.
                with pytest.raises(WebSocketDisconnect) as err:
                    while True:  # a relay may still be finishing a queued frame
                        audio_ws.receive_bytes()
                assert err.value.code == 4411

                # 2. The tracker is cleared: nobody is registered on any surface any more.
                assert c.portal.call(lambda: dict(ch.tracker.viewer_counts)) == {"video": 0, "audio": 0}

                # 3. During the cooldown a new listener is refused with the same code, and /video
                #    answers 403 "Viewers disconnected ..." even for the owner, station or not.
                assert _audio_close_code(c, {VIEWER_COOKIE: viewer_cookie}) == 4411
                now[0] += 30.0  # still inside the 60 s window
                _owner(c, owner_cookie)
                vid = c.get("/video", params={"station": station.station_id})
                assert vid.status_code == 403
                assert "Viewers disconnected" in vid.json()["detail"]

                # 4. After the window lapses the audio surface (viewer role included) comes back.
                now[0] += 61.0
                _owner(c, viewer_cookie)
                with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: viewer_cookie}) as back:
                    assert back.receive_json()["type"] == "audio_hello"

            # The phone hangs up: its close is what ends the uplink (pipeline stops itself).
            phone.send_text(json.dumps({"type": "bye"}))


def test_owner_endpoint_and_phone_uplink_arm_the_same_tracker(tmp_path):
    with TestClient(_config(tmp_path)) as c:
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Buddy"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        station = Station(c, b"OTHERMARKERQ", "PhoneTwo")
        viewer_cookie = _viewer_cookie(c, owner_cookie)

        with c.websocket_connect("/ingest-events") as phone:
            phone.send_text(station.hello())
            challenge = phone.receive_json()
            assert challenge["type"] == "challenge"
            phone.send_text(station.auth(challenge["nonce"]))
            assert phone.receive_json()["type"] == "hello_ack"

            _owner(c, owner_cookie)
            assert c.post("/mode", json={"mode": "full"}).status_code == 200
            phone.send_bytes(bytes([KIND_PREVIEW]) + FAKE_JPEG)

            c.cookies.clear()
            with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: viewer_cookie}) as audio_ws:
                assert audio_ws.receive_json()["type"] == "audio_hello"

                lr = c.portal.call(c.app.state.registry.get, station.household_id)
                ch = c.portal.call(lr.channels.get, station.station_id)
                now = [time.time()]
                c.portal.call(setattr, ch.tracker, "clock", lambda: now[0])

                # Owner-side kick through the dashboards' endpoint...
                _owner(c, owner_cookie)
                kicked = c.post("/viewers/disconnect-all")
                assert kicked.status_code == 200
                assert kicked.json()["cooldown_s"] == 60
                with pytest.raises(WebSocketDisconnect) as err:
                    while True:
                        audio_ws.receive_bytes()
                assert err.value.code == 4411

                # ...and the phone uplink re-arms the very same cooldown with no owner action.
                now[0] += 61.0
                phone.send_text(json.dumps({"type": "kick_viewers"}))
                now[0] += 30.0  # a fresh 60 s window must be running again
                _owner(c, owner_cookie)
                assert c.get("/video").status_code == 403
                now[0] += 31.0  # past this window as well
                _owner(c, viewer_cookie)
                with c.websocket_connect("/audio", cookies={VIEWER_COOKIE: viewer_cookie}) as back:
                    assert back.receive_json()["type"] == "audio_hello"

            phone.send_text(json.dumps({"type": "bye"}))
