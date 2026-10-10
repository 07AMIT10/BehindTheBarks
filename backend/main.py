"""FastAPI app (Person B). Routes only; all behaviour lives in backend/web/runtime.py.

    uvicorn backend.main:app --reload          # pipeline from config.yaml web.pipeline (mock|real|remote)
    DEMO_MODE=1 uvicorn backend.main:app       # LLM off, notifications dashboard-only
    WEB_PIPELINE=remote uvicorn backend.main:app   # the phone produces the events (web.pipeline: remote)
"""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
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
from backend.web.registry import LEGACY, HouseholdRegistry

VIEWER_COOKIE = "btb_session"
STATION_AUTH_PREFIX = b"btb-auth-v1|"  # the signed challenge message: nonce | station_id | device
SESSION_MAX_AGE_S = 31_536_000  # a year: the recovery key, not a password reset, is the escape hatch
HOUSEHOLD_CREATES_PER_HOUR = 5  # web.auth.household_creates_per_hour overrides it
from backend.web.event_log import EventLog
from backend.web.hub import KICKED, Hub
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
        hub = Hub(client_queue=web["ws"]["client_queue"], history=web["ws"]["history"],
                  frame_fps=web["ws"]["frame_fps"], log=EventLog(web["log_dir"]))
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
        registry.use_legacy(rt, hub, rt.pipeline)
        app.state.registry = registry
        app.state.hub, app.state.rt, app.state.demo = hub, rt, demo
        await rt.start()
        try:
            yield
        finally:
            await registry.close()
            await rt.stop()
            hub.close()

    app = FastAPI(title="Behind The Barks", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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
                       runtime=lr.runtime, hub=lr.hub, pipeline=lr.pipeline, request=req)



    async def auth_ctx(req: Request) -> Ctx:
        """The per-request context (T2.2/T2.3): pin mode keeps today's auth and serves the legacy
        household; households mode derives the household from the session cookie. FastAPI awaits
        this dependency, so route bodies never verify auth themselves."""
        if households_mode:
            return await households_ctx(req)
        verify_request_auth(req)
        lr = app.state.registry.peek(LEGACY)
        return Ctx(household_id=LEGACY, role="owner", viewer_id=None,
                   runtime=lr.runtime, hub=lr.hub, pipeline=lr.pipeline, request=req)

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
        session = lr.runtime.phone if lr is not None else None
        if session is not None and getattr(getattr(session, "hello", None), "station_id", None) == station_id:
            closer = getattr(session, "closer", None)
            code_holder = getattr(session, "close_code", None)  # the shared dict the closer reads
            if isinstance(code_holder, dict):
                code_holder["code"] = CLOSE_REVOKED
            if closer is not None:
                await closer()
                lr.runtime.phone_close(session, CLOSE_REVOKED)
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
        rt, hub = lr.runtime, lr.hub
        with app.state.db_session() as s:
            store_repo.touch_station(s, station)

        async def send_downlink(msg: dict) -> None:
            await sock.send_json(msg)

        close_code = {"code": CLOSE_REPLACED}

        async def closer() -> None:
            try:
                await sock.close(code=close_code["code"], reason="station removed")
            except RuntimeError:
                pass

        rem = cfg["web"].get("remote") or {}
        session = RemoteSession(hello, lr.pipeline, rt, clock=rt.clock,
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
        bind_pipeline(lr.pipeline, session, send_downlink)
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
            detach = getattr(lr.pipeline, "detach", None)
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
        resp = JSONResponse({"household_id": household_id, "recovery_key": recovery_key})
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
        return {"enabled": auth_enabled, "auth_required": required, "mode": auth_mode}

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
    async def status(ctx: Ctx = Depends(auth_ctx)) -> dict:
        return ctx.runtime.status()

    @app.get("/events")
    async def events(ctx: Ctx = Depends(auth_ctx), since: int = 0) -> dict:
        return {"events": ctx.hub.history(since), "timeline": ctx.runtime.timeline()}

    @app.post("/treat")
    async def treat(ctx: Ctx = Depends(auth_ctx)) -> dict:
        check_rate_limit("treat", 3.5, ctx.household_id)
        return {"ts": ctx.runtime.treat()}

    @app.post("/torch")
    async def set_torch(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        check_rate_limit("torch", 1.2, ctx.household_id)
        fn = getattr(ctx.pipeline, "set_torch", None)
        if fn is None:
            raise HTTPException(400, "web.pipeline must be 'remote' to control torch")
        if body is not None and "enabled" in body:
            enabled = bool(body["enabled"])
        else:
            get_fn = getattr(ctx.pipeline, "get_torch", None)
            enabled = not (get_fn() if get_fn is not None else False)
        sent = bool(fn(enabled))
        return {"ok": True, "enabled": enabled, "sent": sent}

    @app.get("/torch")
    async def get_torch(ctx: Ctx = Depends(auth_ctx)) -> dict:
        get_fn = getattr(ctx.pipeline, "get_torch", None)
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
    async def camera_flip(ctx: Ctx = Depends(auth_ctx)) -> dict:
        check_rate_limit("flip", 2.5, ctx.household_id)
        fn = getattr(ctx.pipeline, "flip_camera", None)
        if fn is None:
            raise HTTPException(400, "web.pipeline must be 'remote' to flip camera")
        return {"ok": True, "sent": bool(fn())}

    @app.post("/privacy")
    async def set_privacy(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        pipeline = ctx.pipeline
        fn = getattr(pipeline, "set_privacy", None)
        if fn is None:
            raise HTTPException(400, "web.pipeline must be 'remote' to control privacy mode")
        if body is not None and "enabled" in body:
            enabled = bool(body["enabled"])
        else:
            get_fn = getattr(pipeline, "get_privacy", None)
            enabled = not (get_fn() if get_fn is not None else False)
        sent = bool(fn(enabled))
        return {"ok": True, "privacy_mode": enabled, "sent": sent}

    @app.get("/privacy")
    async def get_privacy(ctx: Ctx = Depends(auth_ctx)) -> dict:
        pipeline = ctx.pipeline
        get_fn = getattr(pipeline, "get_privacy", None)
        enabled = False if get_fn is None else bool(get_fn())
        return {"privacy_mode": enabled}

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

    @app.post("/mode")
    async def set_mode(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        try:
            return {"mode": ctx.runtime.set_mode((body or {}).get("mode"))}
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/demo/notify")
    async def demo_notify(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        payload = body or {}
        return await ctx.runtime.demo_notify(payload.get("clip", ""), float(payload.get("t", 0.0)))

    @app.post("/phone/config")
    async def phone_config(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        """Push data.rules overrides to the phone, so retuning needs no APK rebuild (web.pipeline:
        remote only). With no `rules`, the server's own data.rules section is sent."""
        push = getattr(ctx.pipeline, "push_config", None)
        if push is None:
            raise HTTPException(400, "web.pipeline must be 'remote' to push phone config")
        rules = (body or {}).get("rules")
        if rules is not None and not isinstance(rules, dict):
            raise HTTPException(400, "rules must be an object")
        return {"sent": bool(push(rules))}

    @app.get("/video")
    async def video(ctx: Ctx = Depends(auth_ctx), max_frames: int = 0) -> StreamingResponse:
        period = 1.0 / float(web["video"]["fps"])

        async def gen():
            sent = 0
            while max_frames <= 0 or sent < max_frames:
                jpeg = ctx.pipeline.latest_frame_jpeg()
                if jpeg:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() \
                        + b"\r\n\r\n" + jpeg + b"\r\n"
                    sent += 1
                await asyncio.sleep(period)

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
        q = app.state.hub.subscribe()
        try:
            await sock.send_json({"type": "status", "seq": 0, "data": app.state.rt.status()})
            while True:
                await sock.send_json(await q.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
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
        q = lr.hub.subscribe(viewer_id=viewer.id)
        try:
            await sock.send_json({"type": "status", "seq": 0, "data": lr.runtime.status()})
            while True:
                msg = await q.get()
                if msg is KICKED:  # the owner revoked this viewer (T2.6)
                    await sock.close(code=CLOSE_REVOKED, reason="your access was revoked")
                    return
                await sock.send_json(msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
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
