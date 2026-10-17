"""Peek: the 15 s temporary un-blur, and the audit trail it leaves (T3.5, T4.x).

`POST /peek` (backend/main.py) stamps a temporary full-mode window into the station
channel's `Limited` (`backend/web/limited.py`: `start_peek` appends to `audit_log`,
`is_peeking` compares against the injected clock). While a viewer's window is open the
effective exposure is computed as `full` FOR THAT VIEWER ONLY (`GET /mode?station=`,
per-viewer `is_peeking(context.viewer_id)`), and it reverts when the window lapses.

There is no HTTP surface on `Limited.audit_log` (grep), so the recording side is pinned
by object inspection through the portal: each POST /peek must append exactly one audit
entry naming the role, ts, duration and expiry, and refusals (station cap below full)
must NOT be recorded.
"""

import base64
import json
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.store import repo as store_repo
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


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
    """Present the following HTTP calls as this viewer/owner (jar-based, no per-request cookies)."""
    c.cookies.clear()
    c.cookies.set(VIEWER_COOKIE, cookie)


def _viewer_id(app, cookie: str) -> str:
    """The DB viewer id behind a session cookie (the audit entry names exactly this)."""
    from backend.store import repo as _repo

    with app.state.db_session() as s:
        return _repo.viewer_by_token(s, cookie).id


def test_peek_window_is_per_viewer_limited_in_time_and_audited(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 50
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    app = create_app(cfg, pipeline=None, interpreter=None, notifier=DashboardOnlyNotifier())

    with TestClient(app) as c:
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Rex"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        station = Station(c, b"PEEKMARKERBYTES", "PhoneOne")
        code = c.post("/households/me/pairing", json={"kind": "viewer", "label": "Phone"}).json()["code"]
        c.cookies.clear()
        c.post("/join", json={"code": code})
        viewer_cookie = c.cookies[VIEWER_COOKIE]

        with c.websocket_connect("/ingest-events") as phone:
            phone.send_text(station.hello())
            challenge = phone.receive_json()
            assert challenge["type"] == "challenge"
            phone.send_text(station.auth(challenge["nonce"]))
            assert phone.receive_json()["type"] == "hello_ack"

            # Household in limited mode: remote viewers normally see telemetry + blurred frames.
            _owner(c, owner_cookie)
            assert c.post("/mode", json={"mode": "limited"}).status_code == 200

            lr = c.portal.call(c.app.state.registry.get, station.household_id)
            ch = c.portal.call(lr.channels.get, station.station_id)
            assert ch is not None and ch.limited is not None
            now = [time.time()]
            c.portal.call(setattr, ch.limited, "clock", lambda: now[0])

            # Nobody is peeking: effective exposure is the household mode, for everyone.
            _owner(c, viewer_cookie)
            pre = c.get("/mode", params={"station": station.station_id})
            assert pre.json() == {"household_mode": "limited", "station_cap": "full", "effective": "limited"}

            owner_id = _viewer_id(c.app, owner_cookie)
            viewer_id = _viewer_id(c.app, viewer_cookie)
            assert owner_id != viewer_id

            # The VIEWER peeks for 15 s.
            p = c.post("/peek", params={"station": station.station_id})
            assert p.status_code == 200
            assert p.json()["ok"] is True and p.json()["status"] == "peeking"
            assert p.json() == {"ok": True, "status": "peeking", "duration_s": 15.0, "expires_at": now[0] + 15.0}

            # The audit log records exactly one entry naming the real viewer, role and timings.
            assert c.portal.call(lambda: ch.limited.audit_log) == [
                {"viewer_id": viewer_id, "role": "viewer", "ts": now[0],
                 "duration_s": 15.0, "expires_at": now[0] + 15.0}
            ]
            assert c.portal.call(ch.limited.is_peeking, viewer_id) is True

            # Per-viewer exposure: the peeker sees full, every other identity stays limited...
            _owner(c, viewer_cookie)
            own = c.get("/mode", params={"station": station.station_id})
            assert own.json()["effective"] == "full"
            _owner(c, owner_cookie)
            others = c.get("/mode", params={"station": station.station_id})
            assert others.json()["effective"] == "limited"

            # ...and the window lapses exactly at expires_at (fake clock now past it).
            now[0] += 16.0
            assert c.portal.call(ch.limited.is_peeking, viewer_id) is False
            _owner(c, viewer_cookie)
            after = c.get("/mode", params={"station": station.station_id})
            assert after.json()["effective"] == "limited"

            # The owner's peek appends its own entry with the owner's ids and role.
            _owner(c, owner_cookie)
            po = c.post("/peek", params={"station": station.station_id})
            assert po.status_code == 200 and po.json()["expires_at"] == now[0] + 15.0
            assert c.portal.call(lambda: ch.limited.audit_log[-1]) == {
                "viewer_id": owner_id, "role": "owner", "ts": now[0],
                "duration_s": 15.0, "expires_at": now[0] + 15.0}

            # A refusal is NOT audited: with the station capped below full there is no peek.
            phone.send_text(json.dumps({"type": "mode_state", "data": {"cap": "privacy", "effective": "privacy"}}))
            _owner(c, viewer_cookie)
            denied = c.post("/peek", params={"station": station.station_id})
            assert denied.status_code == 409
            assert c.portal.call(len, ch.limited.audit_log) == 2

            phone.send_text(json.dumps({"type": "bye"}))


def test_peek_starts_from_the_household_mode_not_from_a_precursor(tmp_path):
    """A peek is a privilege per call: nothing carries over after its window, and the audit
    log stays instance-scoped (a second household's Limited never sees the first's peeks)."""
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["fps"] = 50
    cfg["web"]["video"]["idle_timeout_s"] = 1.0
    app = create_app(cfg, pipeline=None, interpreter=None, notifier=DashboardOnlyNotifier())

    with TestClient(app) as c:
        c.cookies.clear()
        c.post("/households", json={"dog_name": "Rex"})
        owner_cookie = c.cookies[VIEWER_COOKIE]
        station = Station(c, b"SECONDPEEKMARK", "PhoneOne")

        with c.websocket_connect("/ingest-events") as phone:
            phone.send_text(station.hello())
            challenge = phone.receive_json()
            assert challenge["type"] == "challenge"
            phone.send_text(station.auth(challenge["nonce"]))
            assert phone.receive_json()["type"] == "hello_ack"
            _owner(c, owner_cookie)
            assert c.post("/mode", json={"mode": "limited"}).status_code == 200

            lr = c.portal.call(c.app.state.registry.get, station.household_id)
            ch = c.portal.call(lr.channels.get, station.station_id)
            now = [time.time()]
            c.portal.call(setattr, ch.limited, "clock", lambda: now[0])

            _owner(c, owner_cookie)
            p = c.post("/peek", params={"station": station.station_id})
            assert p.status_code == 200
            # Restart the clock from a much later moment: at the peek's own recorded expiry the
            # window is gone from every surface, no restart of any kind needed.
            now[0] += 200.0
            _owner(c, owner_cookie)
            late = c.get("/mode", params={"station": station.station_id})
            assert late.json()["effective"] == "limited", "no peek echo should outlive its recorded expiry"
            assert c.portal.call(ch.limited.is_peeking, _viewer_id(c.app, owner_cookie)) is False

            # A second household shares no audit log at all.
            c.cookies.clear()
            c.post("/households", json={"dog_name": "Buddy"})
            h2_cookie = c.cookies[VIEWER_COOKIE]
            station2 = Station(c, b"PEEKMARKERH2XY", "PhoneTwo")
            _owner(c, h2_cookie)
            assert c.post("/mode", json={"mode": "limited"}).status_code == 200
            with c.websocket_connect("/ingest-events") as phone2:
                phone2.send_text(station2.hello())
                chal2 = phone2.receive_json()
                assert chal2["type"] == "challenge"
                phone2.send_text(station2.auth(chal2["nonce"]))
                assert phone2.receive_json()["type"] == "hello_ack"

                lr2 = c.portal.call(c.app.state.registry.get, station2.household_id)
                ch2 = c.portal.call(lr2.channels.get, station2.station_id)
                assert c.portal.call(lambda: ch2.limited.audit_log) == []  # its own, empty
                assert c.portal.call(ch2.limited.is_peeking, "dead-or-forgotten-id") is False
                assert c.portal.call(lambda: ch.limited.audit_log) != []   # household 1 keeps its log
                phone2.send_text(json.dumps({"type": "bye"}))

            phone.send_text(json.dumps({"type": "bye"}))
