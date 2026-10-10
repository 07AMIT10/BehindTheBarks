"""FastAPI app (Person B). Routes only; all behaviour lives in backend/web/runtime.py.

    uvicorn backend.main:app --reload          # pipeline from config.yaml web.pipeline (mock|real|remote)
    DEMO_MODE=1 uvicorn backend.main:app       # LLM off, notifications dashboard-only
    WEB_PIPELINE=remote uvicorn backend.main:app   # the phone produces the events (web.pipeline: remote)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import sessionmaker

from backend.fusion.llm_interpreter import LLMInterpreter
from backend.demo.clips import load_demo_clips
from backend.notify import build_notifier
from backend.store import db as store_db
from backend.store import repo as store_repo
from backend.store.models import KIND_STATION, KIND_VIEWER, ROLE_OWNER, ROLE_VIEWER
from backend.store.models import Viewer as _ViewerRow
from backend.web.ingest import CLOSE_REVOKED
from backend.web.auth import Ctx
from backend.web.limited import limit_jpeg
from backend.web.modes import ModeError, min_mode, mode_rank, parse_mode
from backend.web.policy import Policy, build_events_only_slate, build_privacy_slate
from backend.web.registry import LEGACY, HouseholdRegistry

VIEWER_COOKIE = "btb_session"
STATION_AUTH_PREFIX = b"btb-auth-v1|"  # the signed challenge message: nonce | station_id | device
SESSION_MAX_AGE_S = 31_536_000  # a year: the recovery key, not a password reset, is the escape hatch
HOUSEHOLD_CREATES_PER_HOUR = 5  # web.auth.household_creates_per_hour overrides it
from backend.web.debug_metrics import LoopLagMonitor, calculate_drain_pct_per_h, collect_server_metrics
from backend.web.event_log import EventLog
from backend.web.hub import DISCONNECTED_ALL, KICKED, Hub
from backend.web.tracker import CLOSE_VIEWERS_KICKED, parse_ua_label
from backend.web.ingest import CLOSE_BAD_HELLO, CLOSE_BUSY, CLOSE_REPLACED, HelloError, parse_hello
from backend.web.ingest_events import (MAX_SKEW_S, PROTO_STATION, RemoteSession, bind_pipeline,
                                       parse_remote_hello)
from backend.web.profile_store import ProfileStore, validate_profile
from backend.web.runtime import Runtime
from backend.web.settings import LLMSettings, load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("ingest-events")


def build_pipeline(cfg: dict) -> Any:
    if cfg["web"]["pipeline"] == "real":
        from backend.pipeline import Pipeline  # lazy: heavy deps only when asked for

        return Pipeline(cfg)
    if cfg["web"]["pipeline"] == "remote":
        from backend.web.remote_pipeline import RemotePipeline  # lazy: the phone is the producer

        return RemotePipeline(cfg)
    from backend.demo.mock_pipeline import MockPipeline

    return MockPipeline(cfg)


def create_app(cfg: dict | None = None, *, pipeline: Any = None, interpreter: Any = None,
               notifier: Any = None) -> FastAPI:
    if cfg is None:
        load_dotenv()
        cfg = load_config()
    web = cfg["web"]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # In households mode the legacy runtime serves nothing and must not log: every session file
        # belongs to a household directory (T2.7 isolation).
        legacy_log = None if households_mode else EventLog(web["log_dir"])
        hub = Hub(client_queue=web["ws"]["client_queue"], history=web["ws"]["history"],
                  frame_fps=web["ws"]["frame_fps"], log=legacy_log)
        demo = load_demo_clips(cfg)
        # The saved profile wins over the config, so Runtime.status() and captions see the user's values.
        profile_store = ProfileStore(web["log_dir"], defaults=cfg["web"].get("profile") or {})
        app.state.profile_store = profile_store
        cfg["web"]["profile"] = profile_store.load()
        # The household store (T2.1): tables are created here so a fresh deploy self-initialises.
        engine = store_db.make_engine(web.get("database_url", "sqlite:///out/btb.db"))
        store_db.init_db(engine)
        app.state.db_engine = engine
        app.state.db_session = sessionmaker(bind=engine, future=True)
        rt = Runtime(cfg, pipeline or build_pipeline(cfg),
                     interpreter or LLMInterpreter(LLMSettings.from_config(cfg)),
                     notifier or build_notifier(cfg), hub, demo_clips=demo)
        # One Runtime per household (T2.2); the legacy household is the injected one, so
        # app.state.rt / app.state.hub and every existing test keep working unchanged.
        registry = HouseholdRegistry(cfg, clock=rt.clock,
                                     idle_evict_s=float((web.get("registry") or {}).get("idle_evict_s", 600)),
                                     sessionmaker=app.state.db_session)
        lr = registry.use_legacy(rt, hub, rt.pipeline)
        app.state.registry = registry
        app.state.hub, app.state.rt, app.state.demo = hub, rt, demo
        app.state.tracker = lr.tracker
        if not households_mode:
            # In households mode the legacy runtime serves no household, so its loops (and the
            # operator's LLM/notifier it carries) never run at all (T2.7/T2.9).
            await rt.start()
        # T2.10 retention: prune old logs/codes/viewers and drop idle runtimes on a timer.
        sweep_every_s = float((web.get("registry") or {}).get("sweep_every_s", 3600))
        sweeper: asyncio.Task | None = None
        if households_mode and sweep_every_s > 0:

            async def _sweep_forever() -> None:
                while True:
                    await asyncio.sleep(sweep_every_s)
                    try:
                        await registry.sweep()
                    except Exception:  # noqa: BLE001 - a failed sweep must not kill the server
                        continue

            sweeper = asyncio.create_task(_sweep_forever())
        loop_monitor = LoopLagMonitor()
        server_start_time = time.time()
        app.state.loop_monitor = loop_monitor
        app.state.server_start_time = server_start_time
        await loop_monitor.start()
        try:
            yield
        finally:
            loop_monitor.stop()
            if sweeper is not None:
                sweeper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper
            await registry.close()
            await rt.stop()
            hub.close()

    app = FastAPI(title="Behind The Barks", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    debug_cfg = web.get("debug") or {}
    debug_enabled = bool(debug_cfg.get("enabled", False))

    auth_cfg = web.get("auth") or {}
    auth_enabled = bool(auth_cfg.get("enabled", False))
    auth_mode = str(auth_cfg.get("mode", "pin"))
    households_mode = auth_mode == "households"
    dashboard_pin = str(auth_cfg.get("dashboard_pin", "")).strip()
    api_token = str(auth_cfg.get("api_token", "")).strip()
    ingest_token = str(auth_cfg.get("ingest_token", "")).strip()

    def is_valid_token(tok: str | None) -> bool:
        if not auth_enabled:
            return True
        if not tok:
            return False
        tok = tok.strip()
        allowed = {t for t in (dashboard_pin, api_token, ingest_token) if t}
        return tok in allowed

    def verify_request_auth(req: Request) -> None:
        if not auth_enabled:
            return
        auth_hdr = req.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer "):
            bearer = auth_hdr[len("Bearer "):].strip()
            if is_valid_token(bearer):
                return
        x_tok = req.headers.get("X-BTB-Token", "").strip()
        if is_valid_token(x_tok):
            return
        q_tok = req.query_params.get("token", "").strip()
        if is_valid_token(q_tok):
            return
        raise HTTPException(status_code=401, detail="Unauthorized: invalid or missing access token / PIN")

    def verify_ws_auth(sock: WebSocket) -> bool:
        if not auth_enabled:
            return True
        q_tok = sock.query_params.get("token", "").strip()
        if is_valid_token(q_tok):
            return True
        auth_hdr = sock.headers.get("Authorization", "")
        if auth_hdr.startswith("Bearer ") and is_valid_token(auth_hdr[len("Bearer "):]):
            return True
        x_tok = sock.headers.get("X-BTB-Token", "").strip()
        if is_valid_token(x_tok):
            return True
        return False

    # -- households mode (T2.3): cookies, recovery keys, per-household runtimes -------------------
    def rt_clock() -> float:
        return float(getattr(getattr(app.state, "rt", None), "clock", time.time)())

    def _is_https(req: Request) -> bool:
        return req.url.scheme == "https" or req.headers.get("x-forwarded-proto", "").startswith("https")

    def _set_session_cookie(resp, token: str, req: Request) -> None:
        resp.set_cookie(VIEWER_COOKIE, token, httponly=True, samesite="lax", path="/",
                        max_age=SESSION_MAX_AGE_S, secure=_is_https(req))

    def _clear_session_cookie(resp) -> None:
        resp.delete_cookie(VIEWER_COOKIE, path="/")

    def _viewer_token(req: Request) -> str | None:
        """The cookie first (same-origin dashboards); a Bearer token covers the cross-origin dev
        case (?backend=), where browsers refuse to send cookies. URL tokens are gone in this mode."""
        tok = req.cookies.get(VIEWER_COOKIE)
        if tok:
            return tok.strip()
        hdr = req.headers.get("Authorization", "")
        return hdr[len("Bearer "):].strip() if hdr.startswith("Bearer ") else None

    async def households_ctx(req: Request) -> Ctx:
        token = _viewer_token(req)
        if not token:
            raise HTTPException(status_code=401, detail="no session: create a household or use a recovery key")
        with app.state.db_session() as s:
            viewer = store_repo.viewer_by_token(s, token)
            if viewer is None:
                raise HTTPException(status_code=401, detail="session revoked or expired")
            lr = await app.state.registry.get(viewer.household_id)
            store_repo.touch_viewer(s, viewer)
            s.commit()
            return Ctx(household_id=viewer.household_id, role=viewer.role, viewer_id=viewer.id,
                       runtime=lr.runtime, hub=lr.hub, pipeline=lr.pipeline, request=req,
                       policy=lr.policy, tracker=lr.tracker, limited=lr.limited, mode_ceiling=viewer.mode_ceiling or "full")



    async def auth_ctx(req: Request) -> Ctx:
        """The per-request context (T2.2/T2.3): pin mode keeps today's auth and serves the legacy
        household; households mode derives the household from the session cookie. FastAPI awaits
        this dependency, so route bodies never verify auth themselves."""
        if households_mode:
            return await households_ctx(req)
        verify_request_auth(req)
        lr = app.state.registry.peek(LEGACY)
        policy = getattr(lr, "policy", None) or getattr(lr.runtime, "policy", None)
        tracker = getattr(lr, "tracker", None) or getattr(lr.runtime, "tracker", None)
        limited = getattr(lr, "limited", None) or getattr(lr.runtime, "limited", None)
        return Ctx(household_id=LEGACY, role="owner", viewer_id=None,
                   runtime=lr.runtime, hub=lr.hub, pipeline=lr.pipeline, request=req,
                   policy=policy, tracker=tracker, limited=limited, mode_ceiling="full")

    class _BadPair(Exception):
        """An otherwise-invalid /pair attempt: counted, then answered with the same 400 body."""

    # -- station pairing (T2.4): a one-time code buys a station, a signature buys the socket -------
    def _pair_qr(req: Request, code: str) -> dict:
        base = str(req.base_url).rstrip("/")
        ws = base.replace("http://", "ws://").replace("https://", "wss://")
        return {"v": 2, "url": f"{ws}/ingest-events", "pair_url": f"{base}/pair", "code": code}

    def _load_pubkey(spki_b64: str):
        """base64 -> an ECDSA P-256 public key, or None for anything else (no oracle beyond 400)."""
        import base64 as _b64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        try:
            der = _b64.b64decode(spki_b64, validate=True)
        except Exception:
            return None
        if len(der) > 200:
            return None
        try:
            key = serialization.load_der_public_key(der)
        except Exception:
            return None
        return key if isinstance(key, ec.EllipticCurvePublicKey) and key.curve.name == "secp256r1" else None

    def _pair_attempt(req: Request) -> None:
        """10/min per IP and 100/hour globally: every /pair attempt counts, valid or not."""
        now = time.time()
        ip = req.client.host if req.client else "unknown"
        recent = [t for t in pair_failures.get(ip, []) if now - t < 60]
        recent.append(now)
        pair_failures[ip] = recent
        hourly = [t for t in pair_failures_global if now - t < 3600]
        hourly.append(now)
        pair_failures_global[:] = hourly
        if len(recent) > 10 or len(hourly) > 100:
            raise HTTPException(status_code=429, detail="too many pairing attempts; try again later")

    @app.post("/households/me/pairing")
    async def create_pairing(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        """Owner-only: mint a one-time code for a station (or, T2.6, a viewer) and the QR payload."""
        if not households_mode:
            _pin_only_404()
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can pair devices")
        kind = str((body or {}).get("kind") or KIND_STATION)
        if kind not in (KIND_STATION, KIND_VIEWER):
            raise HTTPException(status_code=400, detail="kind must be 'station' or 'viewer'")
        with app.state.db_session() as s:
            code = store_repo.create_pairing_code(s, household_id=ctx.household_id, kind=kind,
                                                  created_by_viewer=ctx.viewer_id,
                                                  mode_ceiling=(body or {}).get("mode_ceiling"),
                                                  viewer_label=(body or {}).get("label"))
            s.commit()
        return {"code": code, "expires_in": int(store_repo.PAIRING_TTL_S), "qr": _pair_qr(ctx.request, code)}

    @app.post("/pair")
    async def pair(req: Request, body: dict | None = None) -> dict:
        """Unauthenticated on purpose: the one-time code is the credential. Always the same 400 on
        failure, so a caller cannot tell a used code from a wrong one."""
        if not households_mode:
            _pin_only_404()
        _pair_attempt(req)
        payload = body or {}
        code = str(payload.get("code") or "").strip()
        spki_b64 = str(payload.get("pubkey_spki_b64") or "")
        device_name = str(payload.get("device_name") or "").strip()
        key = _load_pubkey(spki_b64)
        def _bad_code():
            return JSONResponse(status_code=400, content={"error": "invalid_or_expired_code"})

        try:
            if not code or key is None or not 1 <= len(device_name) <= 64:
                raise _BadPair()
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            with app.state.db_session() as s:
                consumed = store_repo.consume_pairing_code(s, code, kind=KIND_STATION)
                if consumed is None:
                    raise _BadPair()
                station = store_repo.create_station(s, household_id=consumed.household_id, name=device_name,
                                                   pubkey_spki=key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo))
                s.commit()
                return {"station_id": station.id, "household_id": station.household_id,
                        "server_time": rt_clock()}
        except _BadPair:
            return _bad_code()

    @app.delete("/stations/{station_id}")
    async def delete_station(ctx: Ctx = Depends(auth_ctx), station_id: str = "") -> dict:
        """Owner unpairs a station: revoked in the DB and its live socket closed with 4410."""
        if not households_mode:
            _pin_only_404()
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can unpair")
        with app.state.db_session() as s:
            station = store_repo.station_by_id(s, station_id)
            if station is None or station.household_id != ctx.household_id:
                raise HTTPException(status_code=404, detail="unknown station")
            store_repo.revoke_station(s, station_id)
            s.commit()
        lr = app.state.registry.peek(ctx.household_id)
        if lr is not None:
            ch = lr.channels.get(station_id)
            target_rt = ch.runtime if ch is not None else lr.runtime
            session = target_rt.phone
            if session is not None and getattr(getattr(session, "hello", None), "station_id", None) == station_id:
                closer = getattr(session, "closer", None)
                code_holder = getattr(session, "close_code", None)  # the shared dict the closer reads
                if isinstance(code_holder, dict):
                    code_holder["code"] = CLOSE_REVOKED
                if closer is not None:
                    await closer()
                    target_rt.phone_close(session, CLOSE_REVOKED)
            if ch is not None:
                await ch.runtime.stop()
                ch.hub.close()
                del lr.channels[station_id]
                if lr.default_channel_id == station_id:
                    lr.default_channel_id = next(iter(lr.channels.keys())) if lr.channels else None
        return {"ok": True}

    # -- viewer invites (T2.6): a code joins a viewer, the owner lists and revokes ------------------
    @app.post("/join")
    async def join(req: Request, body: dict | None = None) -> dict:
        """Unauthenticated: the one-time viewer code is the credential. A viewer never sees owner
        endpoints, but it may watch within its household."""
        if not households_mode:
            _pin_only_404()
        check_rate_limit("join", 2.0)
        code = str((body or {}).get("code") or "").strip()
        label = str((body or {}).get("label") or "viewer")[:64].strip()
        with app.state.db_session() as s:
            consumed = store_repo.consume_pairing_code(s, code, kind=KIND_VIEWER)
            if consumed is None:
                raise HTTPException(status_code=400, detail="invalid_or_expired_code")
            household_id, ceiling = consumed.household_id, (consumed.mode_ceiling or "full")
            viewer, token = store_repo.create_viewer(
                s, household_id=household_id, role=ROLE_VIEWER, label=label, mode_ceiling=ceiling)
            s.commit()
        resp = JSONResponse({"ok": True, "household_id": household_id})
        _set_session_cookie(resp, token, req)
        return resp

    @app.get("/households/me/members")
    async def members(ctx: Ctx = Depends(auth_ctx)) -> dict:
        """Every member of this household: stations and viewers. Open to any authenticated member."""
        if not households_mode:
            _pin_only_404()
        with app.state.db_session() as s:
            stations = [{"id": st.id, "name": st.name, "last_seen": st.last_seen,
                         "revoked": st.revoked_at is not None}
                        for st in store_repo.stations_of_household(s, ctx.household_id)]
            viewers = [{"id": v.id, "label": v.label, "role": v.role, "mode_ceiling": v.mode_ceiling,
                        "last_seen": v.last_seen, "revoked": v.revoked_at is not None}
                       for v in store_repo.viewers_of_household(s, ctx.household_id)]
        return {"stations": stations, "viewers": viewers}

    @app.delete("/viewers/{viewer_id}")
    async def delete_viewer(ctx: Ctx = Depends(auth_ctx), viewer_id: str = "") -> dict:
        """Owner-only: revoke one viewer (never themselves) and close their live sockets with 4410."""
        if not households_mode:
            _pin_only_404()
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can remove viewers")
        if viewer_id == ctx.viewer_id:
            raise HTTPException(status_code=400, detail="you cannot remove yourself")
        with app.state.db_session() as s:
            viewer = s.get(_ViewerRow, viewer_id)
            if viewer is None or viewer.household_id != ctx.household_id:
                raise HTTPException(status_code=404, detail="unknown viewer")
            if viewer.role == ROLE_OWNER:
                raise HTTPException(status_code=400, detail="the owner cannot be removed")
            store_repo.revoke_viewer(s, viewer_id)
            s.commit()
        lr = app.state.registry.peek(ctx.household_id)
        if lr is not None:
            lr.hub.kick(viewer_id)  # their /ws loop ends; the browser shows the welcome screen again
        return {"ok": True}

    async def _ingest_station_households(sock: WebSocket, hello) -> None:
        """The households-mode handshake: paired station_id, then one signed challenge."""
        import json

        if hello.proto != PROTO_STATION or not hello.station_id:
            await sock.close(code=4401, reason="proto 2 with a paired station_id is required")
            return
        with app.state.db_session() as s:
            station = store_repo.station_by_id(s, hello.station_id)
        if station is None or station.revoked_at is not None:
            await sock.close(code=4401, reason="unknown or revoked station")
            return

        from cryptography.hazmat.primitives import hashes as _hashes
        from cryptography.hazmat.primitives import serialization as _ser
        from cryptography.hazmat.primitives.asymmetric import ec as _ec

        nonce = secrets.token_bytes(32)
        await sock.send_json({"type": "challenge", "nonce": base64.b64encode(nonce).decode(),
                              "server_time": rt_clock()})
        try:
            reply = json.loads(await asyncio.wait_for(sock.receive_text(), timeout=10.0))
        except (asyncio.TimeoutError, TypeError, ValueError):
            await sock.close(code=4401, reason="auth timeout")
            return
        sig_b64 = str(reply.get("sig") or "") if isinstance(reply, dict) else ""
        message = STATION_AUTH_PREFIX + nonce + b"|" + hello.station_id.encode() + b"|" + hello.device.encode()
        try:
            key = _ser.load_der_public_key(station.pubkey_spki)
            key.verify(base64.b64decode(sig_b64, validate=True), message, _ec.ECDSA(_hashes.SHA256()))
        except Exception:
            await sock.close(code=4401, reason="bad signature")
            return

        lr = await app.state.registry.get(station.household_id)
        max_stations = int(cfg.get("web", {}).get("max_stations_per_household", 4) or 4)
        if len(lr.channels) >= max_stations and hello.station_id not in lr.channels:
            await sock.close(code=CLOSE_BUSY, reason="Another phone is already streaming")
            return

        with app.state.db_session() as s:
            store_repo.touch_station(s, station)

        station_name = station.name or hello.device or "Station"
        try:
            channel = await lr.channel(hello.station_id, name=station_name)
        except ValueError:
            await sock.close(code=CLOSE_BUSY, reason="Another phone is already streaming")
            return
        rt, hub, pipeline = channel.runtime, channel.hub, channel.pipeline

        async def send_downlink(msg: dict) -> None:
            await sock.send_json(msg)

        close_code = {"code": CLOSE_REPLACED}

        async def closer() -> None:
            try:
                await sock.close(code=close_code["code"], reason="station removed")
            except RuntimeError:
                pass

        rem = cfg["web"].get("remote") or {}
        session = RemoteSession(hello, pipeline, rt, clock=rt.clock,
                                max_message_bytes=web["ingest"]["max_message_bytes"],
                                fps_window_s=web["ingest"]["fps_window_s"],
                                offset_samples=int(rem.get("clock_samples", 5)),
                                ping_every_s=float(rem.get("ping_every_s", 30.0)),
                                max_skew_s=float(rem.get("max_skew_s", MAX_SKEW_S)))
        session.closer, session.close_code = closer, close_code  # the dict, not a snapshot
        session = rt.phone_open(hello, closer=closer, session=session)
        if session is None:
            await sock.close(code=CLOSE_BUSY, reason="Another phone is already streaming")
            return
        bind_pipeline(pipeline, session, send_downlink)
        code: int | None = None
        try:
            await sock.send_json({"type": "hello_ack", "server_time": session.server_time_at_hello})
            while True:
                msg = await sock.receive()
                if msg["type"] == "websocket.disconnect":
                    code = msg.get("code")
                    break
                data = msg.get("bytes")
                if data is not None:
                    session.handle(data)
                elif msg.get("text") is not None and session.handle_text(msg["text"]) == "ping":
                    await sock.send_json({"type": "pong", "server_time": rt.clock()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            detach = getattr(pipeline, "detach", None)
            if detach is not None:
                detach(session)
            rt.phone_close(session, code)

    def _pin_only_404():
        raise HTTPException(status_code=404, detail="households auth mode is disabled (web.auth.mode: pin)")

    create_limit = int((web.get("auth") or {}).get("household_creates_per_hour", HOUSEHOLD_CREATES_PER_HOUR))

    def _rate_limit_household_creates(req: Request) -> None:
        now = time.time()
        ip = req.client.host if req.client else "unknown"
        recent = [t for t in household_creates.get(ip, []) if now - t < 3600]
        if len(recent) >= create_limit:
            raise HTTPException(status_code=429, detail="too many households from this address; try again later")
        recent.append(now)
        household_creates[ip] = recent

    @app.post("/households")
    async def create_household(req: Request, body: dict | None = None) -> dict:
        if not households_mode:
            _pin_only_404()
        _rate_limit_household_creates(req)
        payload = body or {}
        dog_name = str(payload.get("dog_name") or "Your dog")[:48].strip()
        location = str(payload.get("location") or "")[:48].strip()
        name = str(payload.get("name") or "")[:64].strip()
        with app.state.db_session() as s:
            household, recovery_key = store_repo.create_household(
                s, name=name or "My household", dog_name=dog_name, location=location)
            owner, token = store_repo.create_viewer(s, household_id=household.id, role=ROLE_OWNER, label="owner")
            s.commit()
            household_id = household.id
        resp = JSONResponse({"household_id": household_id, "recovery_key": recovery_key,
                             # T2.9: a new household starts private; the owner opts in explicitly.
                             "llm_enabled": bool(household.llm_enabled),
                             "log_enabled": bool(household.log_enabled)})
        _set_session_cookie(resp, token, req)
        return resp

    @app.post("/auth/recover")
    async def recover(req: Request, body: dict | None = None) -> dict:
        if not households_mode:
            _pin_only_404()
        check_rate_limit("recover", 5.0)
        key = str((body or {}).get("recovery_key") or "").strip()
        with app.state.db_session() as s:
            found = store_repo.recover_household(s, key)
            if found is None:
                raise HTTPException(status_code=401, detail="unknown recovery key")
            viewer, token = found
            s.commit()
        resp = JSONResponse({"ok": True})
        _set_session_cookie(resp, token, req)
        return resp

    @app.post("/auth/logout")
    async def logout(req: Request) -> dict:
        if not households_mode:
            _pin_only_404()
        token = _viewer_token(req)
        with app.state.db_session() as s:
            if token:
                viewer = store_repo.viewer_by_token(s, token)
                if viewer is not None and viewer.role == ROLE_VIEWER:
                    store_repo.revoke_viewer(s, viewer.id)  # a viewer's logout also ends their access
                    s.commit()
        resp = JSONResponse({"ok": True})
        _clear_session_cookie(resp)
        return resp

    @app.get("/me")
    async def me(ctx: Ctx = Depends(auth_ctx)) -> dict:
        if not households_mode:
            _pin_only_404()
        profile = dict(ctx.runtime.cfg["web"].get("profile") or {})
        with app.state.db_session() as s:
            household = store_repo.get_household(s, ctx.household_id)
            name = household.name if household else ""
            from backend.store.models import Viewer

            viewer = s.get(Viewer, ctx.viewer_id) if ctx.viewer_id else None
            ceiling = viewer.mode_ceiling if viewer is not None else "full"
        return {"household_id": ctx.household_id, "role": ctx.role, "name": name, "profile": profile,
                "mode_ceiling": ceiling}

    @app.put("/households/me/profile")
    async def put_household_profile(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        if not households_mode:
            _pin_only_404()
        payload = body or {}
        dog_name = str(payload.get("dog_name") or "Your dog").strip()
        location = str(payload.get("location") or "").strip()
        if len(dog_name) > 48 or len(location) > 48:
            raise HTTPException(status_code=400, detail="dog_name and location must be 48 characters or fewer")
        with app.state.db_session() as s:
            household = store_repo.update_household_profile(s, ctx.household_id, dog_name=dog_name,
                                                            location=location)
            if household is None:
                raise HTTPException(status_code=404, detail="unknown household")
            s.commit()
        profile = dict(ctx.runtime.cfg["web"].get("profile") or {})
        profile.update({"dog_name": dog_name, "location": location})
        ctx.runtime.cfg["web"]["profile"] = profile
        ctx.hub.publish("status", {"profile": profile})
        return profile

    @app.get("/households/me/settings")
    async def get_household_settings(ctx: Ctx = Depends(auth_ctx)) -> dict:
        """A tester household's opt-in flags (T2.9). Defaults are private; the owner opts in."""
        if not households_mode:
            _pin_only_404()
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can change household settings")
        with app.state.db_session() as s:
            household = store_repo.get_household(s, ctx.household_id)
            if household is None:
                raise HTTPException(status_code=404, detail="unknown household")
            return {"llm_enabled": bool(household.llm_enabled),
                    "log_enabled": bool(household.log_enabled)}

    @app.patch("/households/me/settings")
    async def patch_household_settings(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        """Owner-only opt-in for the operator's LLM and for on-disk event logs (T2.9).

        The flags are per household and default to off, so a tester's frames never reach the
        operator's LLM keys or Telegram/WhatsApp tokens unless that tester asked for it.
        """
        if not households_mode:
            _pin_only_404()
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can change household settings")
        payload = body or {}
        llm_enabled = payload.get("llm_enabled")
        log_enabled = payload.get("log_enabled")
        if llm_enabled is not None and not isinstance(llm_enabled, bool):
            raise HTTPException(status_code=400, detail="llm_enabled must be a boolean")
        if log_enabled is not None and not isinstance(log_enabled, bool):
            raise HTTPException(status_code=400, detail="log_enabled must be a boolean")
        with app.state.db_session() as s:
            household = store_repo.update_household_settings(
                s, ctx.household_id,
                llm_enabled=llm_enabled if llm_enabled is not None else None,
                log_enabled=log_enabled if log_enabled is not None else None)
            if household is None:
                raise HTTPException(status_code=404, detail="unknown household")
            s.commit()
            flags = {"llm_enabled": bool(household.llm_enabled),
                     "log_enabled": bool(household.log_enabled)}
        # The running runtime was built with the old flags; drop it so the next request rebuilds
        # the interpreter/notifier from the household's row.
        await app.state.registry.forget(ctx.household_id)
        ctx.hub.publish("status", {"settings": flags})
        return flags

    @app.delete("/households/me")
    async def delete_household_endpoint(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        if not households_mode:
            _pin_only_404()
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can delete the household")
        key = str((body or {}).get("recovery_key") or "").strip()
        with app.state.db_session() as s:
            household = store_repo.get_household(s, ctx.household_id)
            if household is None or not store_db.equal_digest(household.recovery_hash, store_db.sha256_hex(key)):
                raise HTTPException(status_code=400, detail="recovery key does not match")
            store_repo.delete_household(s, ctx.household_id)
            s.commit()
        await app.state.registry.forget(ctx.household_id)
        log_dir = Path(str(ctx.runtime.cfg["web"]["log_dir"]))
        if log_dir.is_dir():  # the household's event-log directory goes with it (data erasure)
            import shutil

            shutil.rmtree(log_dir, ignore_errors=True)
        return {"deleted": True}

    rate_limit_cfg = web.get("rate_limits") or {}
    rate_limits_enabled = bool(rate_limit_cfg.get("enabled", auth_enabled))

    rate_limits: dict[str, float] = {}
    household_creates: dict[str, list[float]] = {}  # per-IP hourly buckets (households mode)
    pair_failures: dict[str, list[float]] = {}  # per-minute /pair attempts
    pair_failures_global: list[float] = []  # per-hour /pair attempts

    def check_rate_limit(action: str, min_interval_s: float, hid: str = LEGACY) -> None:
        if not rate_limits_enabled:
            return
        key = f"{hid}:{action}"  # one household's burst never limits another's
        clock_fn = getattr(getattr(app.state, "rt", None), "clock", time.time)
        now = clock_fn()
        last = rate_limits.get(key, 0.0)
        elapsed = now - last
        if elapsed < min_interval_s:
            wait_s = round(min_interval_s - elapsed, 2)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {action}. Please wait {wait_s}s before retrying."
            )
        rate_limits[key] = now

    @app.get("/auth/status")
    async def auth_status() -> dict:
        required = auth_enabled or households_mode
        return {"enabled": auth_enabled, "auth_required": required, "mode": auth_mode, "debug": debug_enabled}

    def _resolve_channel(lr, station_id: str | None, household_id: str):
        if not station_id:
            return lr.get_channel(None) if lr else None
        if lr and station_id in lr.channels:
            return lr.channels[station_id]
        if hasattr(app.state, "db_session") and app.state.db_session:
            with app.state.db_session() as s:
                st = store_repo.station_by_id(s, station_id)
                if st is None or st.household_id != household_id:
                    raise HTTPException(404, f"station {station_id!r} not found in this household")
        else:
            raise HTTPException(404, f"station {station_id!r} not found in this household")
        return None

    @app.get("/debug/metrics")
    async def debug_metrics(req: Request, station: str | None = None, since: float | None = None) -> dict:
        if not debug_enabled:
            raise HTTPException(status_code=404, detail="Debug mode is disabled")
        ctx = await auth_ctx(req)
        if ctx.role != "owner":
            raise HTTPException(status_code=403, detail="Owner role required for debug metrics")

        clock_fn = getattr(getattr(ctx.runtime, "clock", time.time), "__call__", time.time)
        now = float(clock_fn())
        start_t = getattr(app.state, "server_start_time", now)
        uptime = max(0.0, now - start_t)

        lag_mon = getattr(app.state, "loop_monitor", None)
        loop_lag = getattr(lag_mon, "lag_ms", 0.0) if lag_mon else 0.0

        server = collect_server_metrics(uptime_s=uptime, loop_lag_ms=loop_lag)

        hub = ctx.hub
        clients = hub.client_count
        queue_depths = [q.qsize() for q in getattr(hub, "_clients", set())]

        if households_mode:
            entries = getattr(app.state.registry, "_households", {})
            households_count = len(entries)
        else:
            households_count = 1

        pipeline = ctx.pipeline
        lr = app.state.registry.peek(ctx.household_id) if hasattr(app.state, "registry") else None
        if station:
            ch = _resolve_channel(lr, station, ctx.household_id)
            if ch is not None:
                pipeline = ch.pipeline
        elif lr:
            ch = lr.get_channel(None)
            if ch is not None:
                pipeline = ch.pipeline

        session = getattr(pipeline, "_session", None)
        telemetry_history = list(getattr(session, "telemetry_history", [])) if session else []
        latest_telemetry = getattr(session, "latest_telemetry", None) if session else None

        if since is not None:
            series = [s for s in telemetry_history if float(s.get("ts", 0)) > since]
        else:
            series = telemetry_history

        drain = calculate_drain_pct_per_h(telemetry_history, now=now)

        downlinks_sent = int(getattr(pipeline, "downlinks_sent", 0))
        downlinks_dropped = int(getattr(pipeline, "downlinks_dropped", 0))

        return {
            "server": server,
            "hub": {
                "clients": clients,
                "queue_depths": queue_depths,
            },
            "households": households_count,
            "station": {
                "latest_telemetry": latest_telemetry,
                "series": series,
            },
            "downlinks": {
                "sent": downlinks_sent,
                "dropped": downlinks_dropped,
            },
            "drain_pct_per_h": drain,
        }

    @app.post("/auth/verify")
    async def auth_verify(body: dict | None = None) -> dict:
        if not auth_enabled:
            return {"ok": True, "token": ""}
        payload = body or {}
        submitted = str(payload.get("pin") or payload.get("token") or "").strip()
        if not submitted or not is_valid_token(submitted):
            raise HTTPException(status_code=401, detail="Invalid PIN or access token")
        return {"ok": True, "token": submitted}

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/status")
    async def status(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        if station:
            ch = _resolve_channel(lr, station, ctx.household_id)
            if ch is None:
                return {
                    "phone": None, "state": "idle", "effective_mode": "full",
                    "station_cap": "full", "privacy_mode": False,
                    "pipeline_status": None, "max_idle_s": 180.0,
                    "viewers": {"video": 0, "audio": 0},
                }
            st = dict(ch.runtime.status())
            s_cap = getattr(ch.pipeline, "station_cap", "full")
            eff = min_mode(s_cap, ch.policy.household_mode, getattr(ctx, "mode_ceiling", "full"))
            st["station_cap"] = s_cap
            st["effective_mode"] = eff
            st["privacy_mode"] = eff == "blackout"
            st["max_idle_s"] = float(web.get("video", {}).get("max_idle_s", 180.0))
            st["viewers"] = ch.tracker.viewer_counts if ch.tracker else {"video": 0, "audio": 0}
            st["mode_info"] = {"household_mode": ch.policy.household_mode, "station_cap": s_cap, "effective": eff}
            return st

        ch = _resolve_channel(lr, None, ctx.household_id)
        rt = ch.runtime if ch else ctx.runtime
        pipe = ch.pipeline if ch else ctx.pipeline
        pol = ch.policy if ch else (ctx.policy or getattr(rt, "policy", None))
        tracker = ch.tracker if ch else ctx.tracker

        st = dict(rt.status())
        station_cap = getattr(pipe, "station_cap", "full")
        h_mode = pol.household_mode if pol else getattr(pipe, "_policy_mode", "full")
        effective = min_mode(station_cap, h_mode, getattr(ctx, "mode_ceiling", "full"))
        st["station_cap"] = station_cap
        st["effective_mode"] = effective
        st["privacy_mode"] = effective == "blackout"
        st["max_idle_s"] = float(web.get("video", {}).get("max_idle_s", 180.0))
        st["viewers"] = tracker.viewer_counts if tracker else {"video": 0, "audio": 0}
        st["mode_info"] = {
            "household_mode": h_mode,
            "station_cap": station_cap,
            "effective": effective,
        }

        stations_list = []
        seen_sids = set()
        for s_id, c_obj in lr.channels.items():
            if s_id == "default" and len(lr.channels) > 1 and c_obj.runtime.phone is None:
                continue
            seen_sids.add(s_id)
            sc = getattr(c_obj.pipeline, "station_cap", "full")
            eff_c = min_mode(sc, c_obj.policy.household_mode, getattr(ctx, "mode_ceiling", "full"))
            c_phone = c_obj.runtime.status().get("phone")
            stations_list.append({
                "id": s_id,
                "name": c_obj.name,
                "dog_name": c_obj.runtime.cfg["web"].get("profile", {}).get("dog_name", "Your dog"),
                "phone": c_phone,
                "pipeline_status": c_obj.runtime.status().get("pipeline_status"),
                "effective_mode": eff_c,
                "battery": c_phone.get("battery_pct") if c_phone else None,
            })
        if hasattr(app.state, "db_session") and app.state.db_session and ctx.household_id != LEGACY:
            with app.state.db_session() as s:
                for db_st in store_repo.stations_of_household(s, ctx.household_id):
                    if db_st.revoked_at is None and db_st.id not in seen_sids:
                        seen_sids.add(db_st.id)
                        stations_list.append({
                            "id": db_st.id,
                            "name": db_st.name,
                            "dog_name": db_st.dog_name or (lr.runtime.cfg["web"].get("profile", {}).get("dog_name", "Your dog")),
                            "phone": None,
                            "pipeline_status": None,
                            "effective_mode": "full",
                            "battery": None,
                        })
        st["stations"] = stations_list
        return st

    @app.get("/events")
    async def events(ctx: Ctx = Depends(auth_ctx), since: int = 0, station: str | None = None) -> dict:
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        if station:
            ch = _resolve_channel(lr, station, ctx.household_id)
            if ch is None:
                return {"events": [], "timeline": []}
            return {"events": ch.hub.history(since), "timeline": ch.runtime.timeline()}
        return {"events": ctx.hub.history(since), "timeline": ctx.runtime.timeline()}

    @app.post("/treat")
    async def treat(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        check_rate_limit("treat", 3.5, ctx.household_id)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        rt = ch.runtime if ch else ctx.runtime
        return {"ts": rt.treat()}

    @app.post("/torch")
    async def set_torch(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        check_rate_limit("torch", 1.2, ctx.household_id)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        pipeline = ch.pipeline if ch else ctx.pipeline
        fn = getattr(pipeline, "set_torch", None)
        if fn is None:
            raise HTTPException(400, "web.pipeline must be 'remote' to control torch")
        if body is not None and "enabled" in body:
            enabled = bool(body["enabled"])
        else:
            get_fn = getattr(pipeline, "get_torch", None)
            enabled = not (get_fn() if get_fn is not None else False)
        sent = bool(fn(enabled))
        return {"ok": True, "enabled": enabled, "sent": sent}

    @app.get("/torch")
    async def get_torch(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, station, ctx.household_id)
        pipeline = ch.pipeline if ch else ctx.pipeline
        get_fn = getattr(pipeline, "get_torch", None)
        enabled = False if get_fn is None else bool(get_fn())
        return {"enabled": enabled}

    @app.get("/profile")
    async def get_profile(ctx: Ctx = Depends(auth_ctx)) -> dict:
        """The pet profile every viewer sees: header, captions, toasts."""
        return dict(ctx.runtime.cfg["web"].get("profile") or {})

    @app.put("/profile")
    async def put_profile(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        """Save the viewer's pet name/location/zone label and broadcast it to every dashboard."""
        check_rate_limit("profile", 0.5, ctx.household_id)
        store = getattr(app.state, "profile_store", None)
        if store is None or not isinstance(body, dict):
            raise HTTPException(400, "a JSON body with dog_name/location/zone_label is required")
        try:
            saved = store.save(body)
        except ValueError as err:
            raise HTTPException(400, str(err)) from None
        ctx.runtime.cfg["web"]["profile"] = saved  # Runtime.status() reads this dict: no restart needed
        ctx.hub.publish("status", {"profile": saved})  # every open dashboard, live
        return saved

    @app.post("/camera/flip")
    async def camera_flip(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        check_rate_limit("flip", 2.5, ctx.household_id)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        pipeline = ch.pipeline if ch else ctx.pipeline
        fn = getattr(pipeline, "flip_camera", None)
        if fn is None:
            raise HTTPException(400, "web.pipeline must be 'remote' to flip camera")
        return {"ok": True, "sent": bool(fn())}

    @app.post("/privacy")
    async def set_privacy(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        if ctx.role != ROLE_OWNER:
            raise HTTPException(403, "only the household owner can change the viewing mode")
        enabled = bool((body or {}).get("enabled", False))
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        if target_station and ch is None:
            raise HTTPException(404, f"station {target_station!r} is not active")

        pipeline = ch.pipeline if ch else ctx.pipeline
        policy = ch.policy if ch else (ctx.policy or getattr(ctx.runtime, "policy", None))
        hub = ch.hub if ch else ctx.hub
        if policy is None:
            policy = Policy(household_mode="full")
            if ch:
                ch.policy = policy
            else:
                ctx.policy = policy
                if hasattr(ctx.runtime, "policy"):
                    ctx.runtime.policy = policy

        station_cap = getattr(pipeline, "station_cap", "full")
        if enabled:
            if policy.household_mode != "blackout":
                policy._prev_household_mode = policy.household_mode
            policy.household_mode = "blackout"
            set_policy_fn = getattr(pipeline, "set_policy", None)
            if set_policy_fn:
                set_policy_fn("blackout")
            if not target_station and lr:
                for s_ch in lr.channels.values():
                    s_ch.policy.household_mode = "blackout"
                    if hasattr(s_ch.pipeline, "set_policy"):
                        s_ch.pipeline.set_policy("blackout")
            hub.publish("status", {
                "mode_info": {"household_mode": "blackout", "station_cap": station_cap, "effective": "blackout"},
                "effective_mode": "blackout",
                "privacy_mode": True,
            })
            return {"ok": True, "privacy_mode": True, "status": "applied", "mode": "blackout"}
        else:
            restore_mode = policy.restore_mode()
            if mode_rank(restore_mode) > mode_rank(station_cap):
                req_fn = getattr(pipeline, "request_mode", None)
                if req_fn:
                    req_fn(restore_mode, from_role="owner")
                return {"ok": True, "privacy_mode": True, "status": "pending", "mode": restore_mode}
            else:
                policy.household_mode = restore_mode
                set_policy_fn = getattr(pipeline, "set_policy", None)
                if set_policy_fn:
                    set_policy_fn(restore_mode)
                if not target_station and lr:
                    for s_ch in lr.channels.values():
                        s_ch.policy.household_mode = restore_mode
                        if hasattr(s_ch.pipeline, "set_policy"):
                            s_ch.pipeline.set_policy(restore_mode)
                eff = policy.compute_effective(station_cap, ctx)
                hub.publish("status", {
                    "mode_info": {"household_mode": restore_mode, "station_cap": station_cap, "effective": eff},
                    "effective_mode": eff,
                    "privacy_mode": eff == "blackout",
                })
                return {"ok": True, "privacy_mode": eff == "blackout", "status": "applied", "mode": restore_mode}

    @app.get("/privacy")
    async def get_privacy(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, station, ctx.household_id)
        if station and ch is None:
            raise HTTPException(404, f"station {station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        policy = ch.policy if ch else (ctx.policy or getattr(ctx.runtime, "policy", None))
        station_cap = getattr(pipeline, "station_cap", "full")
        h_mode = policy.household_mode if policy else getattr(pipeline, "_policy_mode", "full")
        effective = min_mode(station_cap, h_mode, getattr(ctx, "mode_ceiling", "full"))
        return {"privacy_mode": effective == "blackout"}

    @app.get("/demo/clips")
    async def demo_clips() -> list[dict]:
        from dataclasses import asdict
        return [asdict(c) for c in app.state.demo.list()]

    @app.get("/demo/clips/{cid}/video")
    async def demo_video(cid: str) -> FileResponse:
        try:
            path = app.state.demo.video_path(cid)
        except KeyError:
            raise HTTPException(404, f"unknown demo clip {cid!r}")
        return FileResponse(path, media_type="video/mp4")

    @app.get("/demo/clips/{cid}/events")
    async def demo_events(cid: str) -> dict:
        try:
            return {"events": app.state.demo.events(cid)}
        except KeyError:
            raise HTTPException(404, f"unknown demo clip {cid!r}")

    @app.get("/demo/clips/{cid}/timeline")
    async def demo_timeline(cid: str) -> dict:
        try:
            tl = app.state.demo.timeline(cid)
        except KeyError:
            raise HTTPException(404, f"unknown demo clip {cid!r}")
        if tl is None:
            raise HTTPException(404, f"no timeline for {cid!r} (run scripts/precompute_demo.py)")
        return tl

    @app.get("/mode")
    async def get_mode(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, station, ctx.household_id)
        if station and ch is None:
            raise HTTPException(404, f"station {station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        policy = ch.policy if ch else (ctx.policy or getattr(ctx.runtime, "policy", None))
        limited = ch.limited if ch else ctx.limited

        station_cap = getattr(pipeline, "station_cap", "full") if pipeline else "full"
        h_mode = policy.household_mode if policy else getattr(pipeline, "_policy_mode", "full") if pipeline else "full"
        is_peeking = limited.is_peeking(ctx.viewer_id or "owner") if limited else False
        base = "full" if is_peeking else h_mode
        effective = min_mode(station_cap, base, getattr(ctx, "mode_ceiling", "full"))
        return {
            "household_mode": h_mode,
            "station_cap": station_cap,
            "effective": effective,
        }

    @app.post("/peek")
    async def post_peek(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        """T3.5: temporary 15s window raising effective mode to min(full, ceiling) if station cap is full."""
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, station, ctx.household_id)
        if station and ch is None:
            raise HTTPException(404, f"station {station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        tracker = ch.tracker if ch else ctx.tracker
        limited = ch.limited if ch else ctx.limited

        station_cap = getattr(pipeline, "station_cap", "full") if pipeline else "full"
        if station_cap != "full":
            raise HTTPException(
                status_code=409,
                detail={"error": "station_cap", "message": f"Peek not allowed while station cap is {station_cap}"}
            )
        if limited is None:
            raise HTTPException(status_code=500, detail="limited processor not available")
        vid = ctx.viewer_id or "owner"
        expires_at = limited.start_peek(vid, role=ctx.role, duration_s=15.0)
        if tracker:
            tracker.push_policy_now()
        return {"ok": True, "status": "peeking", "duration_s": 15.0, "expires_at": expires_at}

    @app.post("/mode")
    async def set_mode(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        if not body or "mode" not in body:
            raise HTTPException(400, "mode is required")
        raw_mode = body.get("mode")
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, target_station, ctx.household_id)

        if raw_mode in ("live", "demo"):
            rt = ch.runtime if ch else ctx.runtime
            try:
                return {"mode": rt.set_mode(raw_mode)}
            except ValueError as exc:
                raise HTTPException(400, str(exc))

        if ctx.role != ROLE_OWNER:
            raise HTTPException(403, "only the household owner can change the viewing mode")

        try:
            mode = parse_mode(raw_mode)
        except ModeError as exc:
            raise HTTPException(400, str(exc))

        if target_station:
            if ch is None:
                raise HTTPException(404, f"station {target_station!r} is not active")
            station_cap = getattr(ch.pipeline, "station_cap", "full")
            if mode_rank(mode) > mode_rank(station_cap):
                req_fn = getattr(ch.pipeline, "request_mode", None)
                if req_fn:
                    req_fn(mode, from_role="owner")
                return {"status": "pending", "mode": mode}
            if ch.policy.household_mode != "blackout":
                ch.policy._prev_household_mode = ch.policy.household_mode
            ch.policy.household_mode = mode
            set_policy_fn = getattr(ch.pipeline, "set_policy", None)
            if set_policy_fn:
                set_policy_fn(mode)
            eff = ch.policy.compute_effective(station_cap, ctx)
            ch.hub.publish("status", {
                "mode_info": {"household_mode": mode, "station_cap": station_cap, "effective": eff},
                "effective_mode": eff,
                "privacy_mode": eff == "blackout",
            })
            return {"status": "applied", "mode": mode}

        station_cap = getattr(ctx.pipeline, "station_cap", "full")
        policy = ctx.policy or getattr(ctx.runtime, "policy", None)
        if policy is None:
            policy = Policy(household_mode="full")
            ctx.policy = policy
            if hasattr(ctx.runtime, "policy"):
                ctx.runtime.policy = policy

        # Raising above station cap sends mode_request {mode, from: "owner"}
        if mode_rank(mode) > mode_rank(station_cap):
            req_fn = getattr(ctx.pipeline, "request_mode", None)
            if req_fn:
                req_fn(mode, from_role="owner")
            return {"status": "pending", "mode": mode}

        # Lowering or at/below cap applies immediately
        if policy.household_mode != "blackout":
            policy._prev_household_mode = policy.household_mode
        policy.household_mode = mode
        set_policy_fn = getattr(ctx.pipeline, "set_policy", None)
        if set_policy_fn:
            set_policy_fn(mode)
        if lr:
            for s_ch in lr.channels.values():
                s_ch.policy.household_mode = mode
                if hasattr(s_ch.pipeline, "set_policy"):
                    s_ch.pipeline.set_policy(mode)
        eff = policy.compute_effective(station_cap, ctx)
        ctx.hub.publish("status", {
            "mode_info": {"household_mode": mode, "station_cap": station_cap, "effective": eff},
            "effective_mode": eff,
            "privacy_mode": eff == "blackout",
        })
        return {"status": "applied", "mode": mode}

    @app.post("/demo/notify")
    async def demo_notify(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        payload = body or {}
        return await ctx.runtime.demo_notify(payload.get("clip", ""), float(payload.get("t", 0.0)))

    @app.post("/phone/config")
    async def phone_config(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        """Push data.rules overrides to the phone, so retuning needs no APK rebuild (web.pipeline:
        remote only). With no `rules`, the server's own data.rules section is sent."""
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        if target_station and ch is None:
            raise HTTPException(404, f"station {target_station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        push = getattr(pipeline, "push_config", None)
        if push is None:
            raise HTTPException(400, "web.pipeline must be 'remote' to push phone config")
        rules = (body or {}).get("rules")
        if rules is not None and not isinstance(rules, dict):
            raise HTTPException(400, "rules must be an object")
        return {"sent": bool(push(rules))}

    @app.post("/viewers/disconnect-all")
    async def disconnect_all_viewers(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        """T3.4: owner disconnects all viewers; imposes a 60s cooldown against /video."""
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can disconnect viewers")
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, station, ctx.household_id)
        if station and ch is None:
            raise HTTPException(404, f"station {station!r} is not active")
        tracker = ch.tracker if ch else ctx.tracker
        count = tracker.kick_all(cooldown_s=60.0) if tracker else 0
        return {"ok": True, "disconnected": count, "cooldown_s": 60}

    @app.post("/focus")
    async def focus(ctx: Ctx = Depends(auth_ctx), body: dict | None = Body(None), station: str | None = None) -> dict:
        """T4.3: Demand-driven preview bandwidth policy: viewers report their active view mode
        ('focus' | 'grid' | 'none') per station or household.
        """
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="view must be 'grid', 'focus', or 'none'")
        view = body.get("view")
        if view not in ("grid", "focus", "none"):
            raise HTTPException(status_code=400, detail="view must be 'grid', 'focus', or 'none'")
        target_station = station or body.get("station")
        viewer_id = ctx.viewer_id or _viewer_token(ctx.request) or "viewer"

        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        if target_station:
            ch = _resolve_channel(lr, target_station, ctx.household_id)
            if ch is None:
                raise HTTPException(status_code=404, detail=f"station {target_station!r} is not active")
            if ch.tracker:
                ch.tracker.set_demand(viewer_id, view)
            eff = ch.tracker.effective_demand if ch.tracker else view
        else:
            if lr and lr.channels:
                for s_ch in lr.channels.values():
                    if s_ch.tracker:
                        s_ch.tracker.set_demand(viewer_id, view)
            elif ctx.tracker:
                ctx.tracker.set_demand(viewer_id, view)
            eff = ctx.tracker.effective_demand if ctx.tracker else view

        return {"status": "ok", "station": target_station, "view": view, "effective": eff}

    @app.get("/video")
    async def video(ctx: Ctx = Depends(auth_ctx), station: str | None = None, view: str | None = None, max_frames: int = 0) -> StreamingResponse:
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        if station:
            ch = _resolve_channel(lr, station, ctx.household_id)
            if ch is None:
                raise HTTPException(404, f"station {station!r} is not active")
        else:
            ch = _resolve_channel(lr, None, ctx.household_id)

        pipeline = ch.pipeline if ch else ctx.pipeline
        policy = ch.policy if ch else (ctx.policy or getattr(ctx.runtime, "policy", None))
        tracker = ch.tracker if ch else ctx.tracker
        limited = ch.limited if ch else ctx.limited

        if tracker and tracker.is_kicked():
            raise HTTPException(status_code=403, detail="Viewers disconnected by station")

        period = 1.0 / float(web["video"]["fps"])
        # A tab left open on a household with no camera must not spin forever: stop after this much
        # silence (also makes ?max_frames=N terminate when no frame ever arrives).
        idle_s = float(web["video"].get("idle_timeout_s", 30.0))

        stream_id = str(uuid.uuid4())
        ua = ctx.request.headers.get("user-agent", "") if ctx.request else ""
        label = parse_ua_label(ua)
        if tracker:
            try:
                tracker.enter_video(stream_id, viewer_id=ctx.viewer_id, label=label, view=view or "focus")
            except Exception:
                raise HTTPException(status_code=403, detail="Viewers disconnected by station")

        async def gen():
            sent = 0
            last_frame_at = time.monotonic()
            try:
                while max_frames <= 0 or sent < max_frames:
                    if tracker and tracker.is_kicked():
                        return
                    station_cap = getattr(pipeline, "station_cap", "full") if pipeline else "full"
                    pol = policy or ctx.policy or getattr(ctx.runtime, "policy", None)
                    h_mode = pol.household_mode if pol else getattr(pipeline, "_policy_mode", "full") if pipeline else "full"
                    is_peeking = limited.is_peeking(ctx.viewer_id or "owner") if limited else False
                    eff_mode = pol.compute_effective(station_cap, ctx, is_peeking=is_peeking) if pol else min_mode(station_cap, "full" if is_peeking else h_mode, getattr(ctx, "mode_ceiling", "full"))

                    if pipeline is None or getattr(pipeline, "_idle", False):
                        jpeg = None
                    elif eff_mode == "blackout":
                        jpeg = getattr(pipeline, "_privacy_jpeg", None) or build_privacy_slate()
                    elif eff_mode == "privacy":
                        jpeg = build_events_only_slate()
                    elif eff_mode == "limited":
                        raw = getattr(pipeline, "raw_frame_jpeg", pipeline.latest_frame_jpeg)()
                        jpeg = limited.process(raw) if (limited and raw) else (limit_jpeg(raw) if raw else None)
                    else:  # full
                        jpeg = getattr(pipeline, "raw_frame_jpeg", pipeline.latest_frame_jpeg)()

                    if jpeg:
                        yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() \
                            + b"\r\n\r\n" + jpeg + b"\r\n"
                        sent += 1
                        last_frame_at = time.monotonic()
                    elif time.monotonic() - last_frame_at > idle_s:
                        return
                    await asyncio.sleep(period)
            finally:
                if tracker:
                    tracker.leave_video(stream_id)

        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.websocket("/ws")
    async def ws(sock: WebSocket) -> None:
        if households_mode:
            await _ws_households(sock)
            return
        if not verify_ws_auth(sock):
            await sock.close(code=4401, reason="Unauthorized")
            return
        await sock.accept()
        q = app.state.hub.subscribe(role="owner")
        tracker = getattr(app.state, "tracker", None)
        if tracker:
            tracker.enter_ws(q, viewer_id="owner", label="Owner")
        try:
            await sock.send_json({"type": "status", "seq": 0, "data": app.state.rt.status()})
            while True:
                msg = await q.get()
                if msg is DISCONNECTED_ALL:
                    await sock.close(code=CLOSE_VIEWERS_KICKED, reason="viewers disconnected by station")
                    return
                await sock.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if tracker:
                tracker.leave_ws(q)
            app.state.hub.unsubscribe(q)

    async def _ws_households(sock: WebSocket) -> None:
        """Households mode: the cookie is the credential, and the subscriber only ever reaches its
        own household's hub (T2.3). A Bearer token also works for the cross-origin dev case."""
        token = (sock.cookies.get(VIEWER_COOKIE) or "").strip()
        if not token:
            hdr = sock.headers.get("Authorization", "")
            token = hdr[len("Bearer "):].strip() if hdr.startswith("Bearer ") else ""
        await sock.accept()
        if not token:
            await sock.close(code=4401, reason="Unauthorized: no session")
            return
        with app.state.db_session() as s:
            viewer = store_repo.viewer_by_token(s, token)
        if viewer is None:
            await sock.close(code=4401, reason="Unauthorized: session revoked or expired")
            return
        lr = await app.state.registry.get(viewer.household_id)
        q = lr.hub.subscribe(viewer_id=viewer.id, role=viewer.role)
        ua = sock.headers.get("user-agent", "")
        label = viewer.role if viewer.role == "owner" else parse_ua_label(ua)
        if lr.tracker:
            lr.tracker.enter_ws(q, viewer_id=viewer.id, label=label)
        try:
            status_data = dict(lr.runtime.status())
            eff = lr.policy.compute_effective(getattr(lr.pipeline, "station_cap", "full"), viewer)
            status_data["effective_mode"] = eff
            if "mode_info" in status_data and isinstance(status_data["mode_info"], dict):
                status_data["mode_info"] = dict(status_data["mode_info"])
                status_data["mode_info"]["effective"] = eff
            status_data["privacy_mode"] = eff == "blackout"
            await sock.send_json({"type": "status", "seq": 0, "data": status_data})
            while True:
                msg = await q.get()
                if msg is KICKED:  # the owner revoked this viewer (T2.6)
                    await sock.close(code=CLOSE_REVOKED, reason="your access was revoked")
                    return
                if msg is DISCONNECTED_ALL:  # station or owner kicked viewers (T3.4)
                    await sock.close(code=CLOSE_VIEWERS_KICKED, reason="viewers disconnected by station")
                    return
                if msg.get("type") == "status" and isinstance(msg.get("data"), dict):
                    data = dict(msg["data"])
                    eff = lr.policy.compute_effective(getattr(lr.pipeline, "station_cap", "full"), viewer)
                    data["effective_mode"] = eff
                    if "mode_info" in data and isinstance(data["mode_info"], dict):
                        data["mode_info"] = dict(data["mode_info"])
                        data["mode_info"]["effective"] = eff
                    data["privacy_mode"] = eff == "blackout"
                    msg = {**msg, "data": data}
                await sock.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if lr.tracker:
                lr.tracker.leave_ws(q)
            lr.hub.unsubscribe(q)

    @app.websocket("/ingest")
    async def ingest(sock: WebSocket) -> None:
        """Phone camera: JSON hello, then binary 0x01+JPEG / 0x02+PCM16 (backend/web/ingest.py)."""
        await sock.accept()
        rt = app.state.rt
        first = await sock.receive()
        if first["type"] == "websocket.disconnect":
            return
        try:
            hello = parse_hello(first.get("text"))
        except HelloError as err:
            await sock.close(code=CLOSE_BAD_HELLO, reason=str(err))
            return

        if auth_enabled and ingest_token:
            q_tok = sock.query_params.get("token", "").strip()
            if q_tok != ingest_token:
                await sock.close(code=4401, reason="Unauthorized ingest token")
                return

        async def close_replaced() -> None:
            try:
                await sock.close(code=CLOSE_REPLACED, reason="Replaced by a newer phone connection")
            except RuntimeError:
                pass  # already closed

        session = rt.phone_open(hello, closer=close_replaced)
        if session is None:
            await sock.close(code=CLOSE_BUSY, reason="Another phone is already streaming")
            return
        code: int | None = None
        try:
            while True:
                msg = await sock.receive()
                if msg["type"] == "websocket.disconnect":
                    code = msg.get("code")
                    break
                data = msg.get("bytes")
                if data is not None:
                    session.handle(data)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rt.phone_close(session, code)

    @app.websocket("/ingest-events")
    async def ingest_events(sock: WebSocket) -> None:
        """Phone that runs the perception itself: JSON hello, then JSON envelopes plus binary
        0x01 preview JPEGs (backend/web/ingest_events.py). Same close codes and one-phone-at-a-time
        rules as /ingest."""
        if households_mode:
            await sock.accept()  # receive() before accept() only yields the connect event
            first0 = await sock.receive()
            if first0["type"] == "websocket.disconnect":
                return
            try:
                hello0 = parse_remote_hello(first0.get("text"))
            except HelloError as err:
                await sock.close(code=CLOSE_BAD_HELLO, reason=str(err))
                return
            await _ingest_station_households(sock, hello0)
            return
        await sock.accept()
        rt = app.state.rt
        if rt.cfg["web"]["pipeline"] != "remote":
            log.warning("a phone is streaming events but web.pipeline is %r; run WEB_PIPELINE=remote so "
                        "the server keeps no models and starts no watchdog", rt.cfg["web"]["pipeline"])
        first = await sock.receive()
        if first["type"] == "websocket.disconnect":
            return
        try:
            hello = parse_remote_hello(first.get("text"))
        except HelloError as err:
            await sock.close(code=CLOSE_BAD_HELLO, reason=str(err))
            return

        if auth_enabled and ingest_token:
            q_tok = sock.query_params.get("token", "").strip()
            h_tok = (hello.token or "").strip()
            if q_tok != ingest_token and h_tok != ingest_token:
                await sock.close(code=4401, reason="Unauthorized ingest token")
                return

        async def send_downlink(msg: dict) -> None:
            await sock.send_json(msg)

        async def close_replaced() -> None:
            try:
                await sock.close(code=CLOSE_REPLACED, reason="Replaced by a newer phone connection")
            except RuntimeError:
                pass  # already closed

        rem = cfg["web"].get("remote") or {}
        session = RemoteSession(hello, rt.pipeline, rt, clock=rt.clock,
                                max_message_bytes=web["ingest"]["max_message_bytes"],
                                fps_window_s=web["ingest"]["fps_window_s"],
                                offset_samples=int(rem.get("clock_samples", 5)),
                                ping_every_s=float(rem.get("ping_every_s", 30.0)),
                                max_skew_s=float(rem.get("max_skew_s", MAX_SKEW_S)))
        session = rt.phone_open(hello, closer=close_replaced, session=session)
        if session is None:
            await sock.close(code=CLOSE_BUSY, reason="Another phone is already streaming")
            return
        bind_pipeline(rt.pipeline, session, send_downlink)
        code: int | None = None
        try:
            await sock.send_json({"type": "hello_ack", "server_time": session.server_time_at_hello})
            while True:
                msg = await sock.receive()
                if msg["type"] == "websocket.disconnect":
                    code = msg.get("code")
                    break
                data = msg.get("bytes")
                if data is not None:
                    session.handle(data)
                elif msg.get("text") is not None and session.handle_text(msg["text"]) == "ping":
                    await sock.send_json({"type": "pong", "server_time": rt.clock()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            detach = getattr(rt.pipeline, "detach", None)
            if detach is not None:
                detach(session)
            rt.phone_close(session, code)

    frontend_out = Path(__file__).resolve().parent.parent / "frontend" / "out"
    if frontend_out.is_dir():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=str(frontend_out), html=True), name="frontend")

    return app


app = create_app()
