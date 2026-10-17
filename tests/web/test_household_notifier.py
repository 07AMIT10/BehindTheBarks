"""The wired household Telegram reporter (privacy fix #1): in households mode the household's
runtime actually reports to Telegram via its stored subscriber rows — muted per subscriber row.

Covers:
1. Behaviour (real class, fake transport): notify_barks=False mutes ordinary alert kinds but keeps
   distress honouring notify_distress=True; fully-muted rows receive nothing.
2. Wiring: a newly created household in households mode carries an external, Telegram-backed
   reporter (the injected stub factory records the build), on the household runtime and on each
   station channel; a bark state actually flows through it. Default config stays dashboard-only.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import urllib.parse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import backend.main as bmain
from backend.contracts import EmotionState
from backend.main import create_app
from backend.notify.base import NotifyResult
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.notify.telegram import HouseholdTelegramNotifier
from backend.store import db as store_db
from backend.store import repo as store_repo
from backend.web.settings import load_config

AUTH_PREFIX = b"btb-auth-v1|"


class NoLLM:
    enabled = False
    last_call = None


def _state(emotion: str) -> EmotionState:
    return EmotionState(ts=1000.0, emotion=emotion, confidence=0.9, source="rules", reason="t")


def test_subscriber_mutes_decide_who_gets_barks_and_distress(tmp_path):
    engine = store_db.make_engine(f"sqlite:///{tmp_path / 'tg.db'}")
    store_db.init_db(engine)
    Session = sessionmaker(bind=engine, future=True)
    with Session() as s:
        household, _key = store_repo.create_household(s, recovery_key="k")
        hid = household.id
        store_repo.add_telegram_subscriber(s, hid, "-100MUTED", notify_barks=False)
        store_repo.add_telegram_subscriber(s, hid, "-100LOUD")
        s.commit()
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(urllib.parse.parse_qsl(request.content.decode()))
        calls.append((str(request.url).split("/botTESTTOK/")[1], body.get("chat_id")))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    n = HouseholdTelegramNotifier("TESTTOK", household_id=hid, db_session_factory=Session,
                                  client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    # excited = an ordinary bark-kind alert: the muted chat must not receive it, the loud one does
    res = asyncio.run(n.send(_state("excited"), None))
    assert [chat for _m, chat in calls] == ["-100LOUD"]
    assert res.status == "sent"
    # fearful = distress: notify_distress defaults True for both rows -> both chats
    asyncio.run(n.send(_state("fearful"), None))
    assert sorted(chat for _m, chat in calls[-2:]) == ["-100LOUD", "-100MUTED"]

    # a fully muted row: barks muted, distress muted -> excited/fearful never reach it
    with Session() as s:
        store_repo.add_telegram_subscriber(s, hid, "-100ALLMUTED", notify_barks=False, notify_distress=False)
        s.commit()
    calls.clear()
    asyncio.run(n.send(_state("excited"), None))
    assert "-100ALLMUTED" not in [chat for _m, chat in calls]
    asyncio.run(n.send(_state("fearful"), None))
    assert "-100ALLMUTED" not in [chat for _m, chat in calls]


class SpyTelegram:
    """The stand-in the wiring test injects: records that the factory built a Telegram-backed
    reporter for the household, and that the runtime actually reports through it."""

    is_external = True
    built: list["SpyTelegram"] = []

    def __init__(self, *, token, profile=None, household_id=None, db_session_factory=None, **kw):
        self.token, self.household_id, self.db_session_factory = token, household_id, db_session_factory
        self.sent = []
        SpyTelegram.built.append(self)

    async def send(self, state, jpeg, **kw):
        self.sent.append((state.emotion, jpeg))
        return NotifyResult("sent", "telegram")


@pytest.fixture()
def spy_on_factory(monkeypatch):
    """Swap the real Telegram reporter the household factory builds for the recording stub."""
    SpyTelegram.built = []
    monkeypatch.setattr(bmain, "HouseholdTelegramNotifier", SpyTelegram)
    return SpyTelegram


def _households_app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["idle_timeout_s"] = 0.5
    cfg["web"]["notify"]["mode"] = "telegram"
    cfg["web"]["notify"]["telegram"]["token"] = "TESTTOK"
    return create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


class Station:
    """A paired station with the signed /ingest-events handshake (as in test_isolation)."""

    def __init__(self, client, device="Phone"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.device = device
        code = client.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        spki = base64.b64encode(
            self.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()
        r = client.post("/pair", json={"code": code, "pubkey_spki_b64": spki, "device_name": device}).json()
        self.station_id, self.household_id = r["station_id"], r["household_id"]

    def hello(self) -> str:
        return json.dumps({"type": "hello", "proto": 2, "station_id": self.station_id,
                           "device": self.device, "phone_time": time.time(), "models": {},
                           "profile": "mobile"})

    def auth(self, nonce_b64: str) -> str:
        msg = AUTH_PREFIX + base64.b64decode(nonce_b64) + b"|" + self.station_id.encode() + b"|" + self.device.encode()
        sig = base64.b64encode(self.key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
        return json.dumps({"type": "auth", "sig": sig})


@contextlib.contextmanager
def _connect_station(c, station: Station):
    """Drive the full station handshake on a `with`-entered socket."""
    with c.websocket_connect("/ingest-events") as ws:
        ws.send_text(station.hello())
        challenge = ws.receive_json()
        assert challenge["type"] == "challenge"
        ws.send_text(station.auth(challenge["nonce"]))
        assert ws.receive_json()["type"] == "hello_ack"
        yield ws


def test_a_telegram_subscribed_household_reports_through_a_telegram_backed_reporter(
        spy_on_factory, tmp_path):
    app_ = _households_app(tmp_path)
    with TestClient(app_) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        station = Station(c)
        with _connect_station(c, station):
            c.get("/mode")  # materialize the household runtime in the registry
            lr = app_.state.registry.peek(hid)
            assert lr is not None
            ch = lr.channels.get(station.station_id)
            assert ch is not None
            # both the household runtime and its station channel carry the factory-built reporter
            for rt in (lr.runtime, ch.runtime):
                assert isinstance(rt.notifier, SpyTelegram)
                assert rt.notifier.household_id == hid
                assert rt.notifier.db_session_factory == app_.state.db_session
            # a bark state actually flows through the Telegram-backed reporter
            asyncio.run(ch.runtime._notify(_state("excited")))
            assert ch.runtime.notifier.sent and ch.runtime.notifier.sent[-1][0] == "excited"


def test_default_households_config_stays_dashboard_only(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["idle_timeout_s"] = 0.5
    SpyTelegram.built = []
    app_ = create_app(cfg, pipeline=None, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app_) as c:
        c.post("/households", json={"dog_name": "Rex"})
        c.get("/mode")
        hid = c.get("/me").json()["household_id"]
        lr = app_.state.registry.peek(hid)
        assert type(lr.runtime.notifier) is DashboardOnlyNotifier  # no inherited operator channels
        assert SpyTelegram.built == []  # and the factory built nothing Telegram-backed
