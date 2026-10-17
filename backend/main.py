"""FastAPI app (Person B). Routes only; all behaviour lives in backend/web/runtime.py.

    uvicorn backend.main:app --reload          # pipeline from config.yaml web.pipeline (mock|real|remote)
    DEMO_MODE=1 uvicorn backend.main:app       # LLM off, notifications dashboard-only
    WEB_PIPELINE=remote uvicorn backend.main:app   # the phone produces the events (web.pipeline: remote)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
import httpx
from urllib.parse import urlparse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import sessionmaker

from backend.fusion.llm_interpreter import LLMInterpreter
from backend.demo.clips import load_demo_clips
from backend.notify import build_notifier
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.notify.telegram import HouseholdTelegramNotifier
from backend.store import db as store_db
from backend.store import repo as store_repo
from backend.store.models import KIND_STATION, KIND_VIEWER, ROLE_OWNER, ROLE_VIEWER, Household
from backend.store.models import Viewer as _ViewerRow
from backend.web.ingest import CLOSE_REVOKED
from backend.web.auth import Ctx
from backend.web.limited import limit_jpeg
from backend.web.limits import RateLimiter
from backend.web.security import SecurityMiddleware, build_csp, inline_script_hashes
from backend.web.modes import ModeError, min_mode, mode_rank, parse_mode
from backend.web.policy import Policy, build_events_only_slate, build_privacy_slate
from backend.web.registry import LEGACY, HouseholdRegistry
from backend.web.compression import ToggleGzip
from backend.store.storage import build_r2
from backend.demo.clips import demo_object_key
from backend.web.tuning import DbTuningStore, Tuning, TuningError

VIEWER_COOKIE = "btb_session"
STATION_AUTH_PREFIX = b"btb-auth-v1|"  # the signed challenge message: nonce | station_id | device
SESSION_MAX_AGE_S = 31_536_000  # a year: the recovery key, not a password reset, is the escape hatch
HOUSEHOLD_CREATES_PER_HOUR = 5  # web.auth.household_creates_per_hour overrides it
from backend.web.debug_metrics import LoopLagMonitor, calculate_drain_pct_per_h, collect_server_metrics
from backend.web.event_log import EventLog
from backend.web.audio_hub import CLIENT_GONE, KICKED_AUDIO, AudioHub, _put_drop_oldest
from backend.web.hub import DISCONNECTED_ALL, KICKED, Hub
from backend.web.tracker import CLOSE_VIEWERS_KICKED, ViewersKickedError, parse_ua_label
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


def make_household_notifier(cfg: dict, household_id: str, sessionmaker: Any) -> Any:
    """One household's external notifier, from the household's own config and its stored Telegram
    subscriber rows (the submodule the webhook writes). The Telegram part lives in the household:
    it loads the subscriber chats and their per-subscriber mutes from the DB at send time, carries
    the `household_id` for the inline buttons, and never uses the operator's WEBHOOK/token chat ids.

    Legacy/PIN mode never calls this (the registry wraps the injected operator notifier there)."""
    notify_cfg = (cfg.get("web") or {}).get("notify") or {}
    mode = str(notify_cfg.get("mode") or "dashboard_only").strip().lower()
    tg_cfg = notify_cfg.get("telegram") if isinstance(notify_cfg.get("telegram"), dict) else {}
    token = str(tg_cfg.get("token") or "").strip()
    if mode in ("dashboard_only", "whatsapp") or not token:
        return DashboardOnlyNotifier()
    return HouseholdTelegramNotifier(
        token=token,
        profile=dict((cfg.get("web") or {}).get("profile") or {}),
        household_id=household_id,
        db_session_factory=sessionmaker,
    )


def create_app(cfg: dict | None = None, *, pipeline: Any = None, interpreter: Any = None,
               notifier: Any = None) -> FastAPI:
    if cfg is None:
        load_dotenv()
        cfg = load_config()
    web = cfg["web"]
    # Admin-tunable bandwidth knobs: config.yaml is the baseline, PATCH /admin/tuning overrides it live.
    # The overrides are stored in the database (attached in the lifespan, once the engine exists).
    tuning = Tuning(
        {"video_fps": web["video"]["fps"], "ws_frame_fps": web["ws"]["frame_fps"],
         "gzip_enabled": (web.get("compression") or {}).get("enabled", True),
         "gzip_min_size": (web.get("compression") or {}).get("min_size", 1000)},
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # In households mode the legacy runtime serves nothing and must not log: every session file
        # belongs to a household directory (T2.7 isolation).
        legacy_log = None if households_mode else EventLog(web["log_dir"])
        hub = Hub(client_queue=web["ws"]["client_queue"], history=web["ws"]["history"],
                  frame_fps=lambda: tuning.get("ws_frame_fps"), log=legacy_log)
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
        # Overrides come from the database; a pre-database out/tuning.json is imported once.
        tuning.attach(DbTuningStore(app.state.db_session), legacy_path=Path(str(web["log_dir"])) / "tuning.json")
        rt = Runtime(cfg, pipeline or build_pipeline(cfg),
                     interpreter or LLMInterpreter(LLMSettings.from_config(cfg)),
                     notifier or build_notifier(cfg), hub, demo_clips=demo)
        # One Runtime per household (T2.2); the legacy household is the injected one, so
        # app.state.rt / app.state.hub and every existing test keep working unchanged.
        registry = HouseholdRegistry(cfg, clock=rt.clock,
                                     idle_evict_s=float((web.get("registry") or {}).get("idle_evict_s", 600)),
                                     sessionmaker=app.state.db_session, tuning=tuning,
                                     make_notifier=lambda hcfg, hid: make_household_notifier(
                                         hcfg, hid, app.state.db_session))
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

        async def _tuning_forever() -> None:
            # Re-read the admin overrides, so a change saved through the other host reaches this one.
            while True:
                await asyncio.sleep(30)
                await asyncio.to_thread(tuning.refresh)

        tuning_refresh = asyncio.create_task(_tuning_forever())
        loop_monitor = LoopLagMonitor()
        server_start_time = time.time()
        app.state.loop_monitor = loop_monitor
        app.state.server_start_time = server_start_time
        await loop_monitor.start()
        try:
            yield
        finally:
            loop_monitor.stop()
            tuning_refresh.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tuning_refresh
            if sweeper is not None:
                sweeper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sweeper
            await registry.close()
            await rt.stop()
            hub.close()

    app = FastAPI(title="Behind The Barks", lifespan=lifespan)

    debug_cfg = web.get("debug") or {}
    debug_enabled = bool(debug_cfg.get("enabled", False))

    auth_cfg = web.get("auth") or {}
    auth_enabled = bool(auth_cfg.get("enabled", False))
    auth_mode = str(auth_cfg.get("mode", "pin"))
    households_mode = auth_mode == "households"
    cors_origins = [str(o).strip().rstrip("/") for o in (web.get("cors_origins") or []) if str(o).strip()]
    if not households_mode:
        app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    elif cors_origins:
        # credentialed requests cannot use "*": only the listed dev origins (web.cors_origins) get CORS
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_credentials=True,
                           allow_methods=["*"], allow_headers=["*"])
    # H2/H3: security headers, dashboard CSP, households Origin check, TLS-only mode (backend/web/security.py)
    frontend_out = Path(__file__).resolve().parent.parent / "frontend" / "out"
    force_https = bool(web.get("force_https", False))
    csp_cfg = web.get("csp") or {}
    csp_policy = None
    if bool(csp_cfg.get("enabled", True)):
        extra = [str(o).strip().rstrip("/") for o in (csp_cfg.get("extra_connect_src") or []) if str(o).strip()]
        csp_policy = build_csp(inline_script_hashes(frontend_out), [*cors_origins, *extra])
    app.add_middleware(SecurityMiddleware, csp=csp_policy, force_https=force_https,
                       origin_check=households_mode, allowed_origins=cors_origins)
    app.add_middleware(ToggleGzip, tuning=tuning)
    app.state.tuning = tuning

    def _read_frontend_build_id() -> str:
        candidates = [
            frontend_out / "BUILD_ID",
            frontend_out.parent / ".next" / "BUILD_ID",
        ]
        for c in candidates:
            if c.is_file():
                try:
                    bid = c.read_text(encoding="utf-8").strip()
                    if bid:
                        return bid
                except Exception:
                    pass
        index_file = frontend_out / "index.html"
        if index_file.is_file():
            try:
                return hex(int(index_file.stat().st_mtime))[2:]
            except Exception:
                pass
        return ""

    app.state.frontend_build_id = _read_frontend_build_id()

    @app.middleware("http")
    async def request_timeout_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith(("/video", "/ws", "/ingest-events", "/audio")):
            response = await call_next(request)
            response.headers.setdefault("Keep-Alive", "timeout=75, max=1000")
            return response
        try:
            response = await asyncio.wait_for(call_next(request), timeout=25.0)
            response.headers.setdefault("Keep-Alive", "timeout=75, max=1000")
            return response
        except asyncio.TimeoutError:
            log.warning("HTTP request timed out after 25s: %s %s", request.method, path)
            return JSONResponse(status_code=504, content={"error": "gateway_timeout", "detail": "Request timed out after 25 seconds"})

    # Demo MP4s: local files (default) or presigned R2 URLs. Misconfiguration fails at startup, not in the demo.
    demo_source = str((web.get("demo") or {}).get("video_source", "local")).strip().lower()
    if demo_source not in {"local", "r2"}:
        raise ValueError(f"web.demo.video_source must be 'local' or 'r2', not {demo_source!r}")
    app.state.demo_r2 = build_r2(cfg) if demo_source == "r2" else None
    if demo_source == "r2" and app.state.demo_r2 is None:
        raise ValueError("web.demo.video_source is 'r2' but R2 is not configured (set BTB_R2_* in the environment)")
    dashboard_pin = str(auth_cfg.get("dashboard_pin", "")).strip()
    api_token = str(auth_cfg.get("api_token", "")).strip()
    ingest_token = str(auth_cfg.get("ingest_token", "")).strip()

    # -- Telegram webhook: the expected `X-Telegram-Bot-Api-Secret-Token` (H: constant-time check)--
    tg_notify_cfg = (web.get("notify") or {}).get("telegram") or {}
    tg_webhook_secret = str(os.environ.get("TELEGRAM_WEBHOOK_SECRET")
                            or tg_notify_cfg.get("webhook_secret") or "").strip()
    if (os.environ.get("TELEGRAM_BOT_TOKEN") or tg_notify_cfg.get("token")) and not tg_webhook_secret:
        logging.getLogger("notify").warning(
            "TELEGRAM_WEBHOOK_SECRET / web.notify.telegram.webhook_secret is not set while a bot "
            "token is: POST /telegram/webhook cannot authenticate its callers. Set the secret and "
            "pass it as `secret_token` to setWebhook.")

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
                        max_age=SESSION_MAX_AGE_S, secure=force_https or _is_https(req))

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

    _last_viewer_touch: dict[str, float] = {}
    _viewer_cache: dict[str, tuple[float, tuple[str, str, str, str]]] = {}

    async def households_ctx(req: Request) -> Ctx:
        token = _viewer_token(req)
        if not token:
            raise HTTPException(status_code=401, detail="no session: create a household or use a recovery key")
        now = time.monotonic()
        cached = _viewer_cache.get(token)
        if cached and (now - cached[0] < 20.0):
            viewer_id, household_id, role, mode_ceiling = cached[1]
        else:
            with app.state.db_session() as s:
                viewer = store_repo.viewer_by_token(s, token)
                if viewer is None:
                    _viewer_cache.pop(token, None)
                    raise HTTPException(status_code=401, detail="session revoked or expired")
                viewer_id = viewer.id
                household_id = viewer.household_id
                role = viewer.role
                mode_ceiling = viewer.mode_ceiling or "full"
                if now - _last_viewer_touch.get(viewer.id, 0.0) > 30.0:
                    _last_viewer_touch[viewer.id] = now
                    store_repo.touch_viewer(s, viewer)
                    s.commit()
            _viewer_cache[token] = (now, (viewer_id, household_id, role, mode_ceiling))

        lr = await app.state.registry.get(household_id)
        return Ctx(household_id=household_id, role=role, viewer_id=viewer_id,
                   runtime=lr.runtime, hub=lr.hub, pipeline=lr.pipeline, request=req,
                   policy=lr.policy, tracker=lr.tracker, limited=lr.limited, mode_ceiling=mode_ceiling)



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

    def _client_ip(req: Request) -> str:
        return req.client.host if req.client else "unknown"

    # One limiter for every rate limit and lockout in this app (H4, backend/web/limits.py).
    limiter = RateLimiter(clock=rt_clock)
    LOCKOUT_DETAIL = "too many failed attempts; try again later"  # generic: no oracle on the code

    def _lockout_key(name: str, req: Request) -> str:
        """Refuse with 429 + Retry-After while this client IP is locked out for `name` (the failed
        attempts of /pair, /auth/recover and /join each have their own per-IP counter)."""
        key = f"{name}:{_client_ip(req)}"
        wait = limiter.locked_for(key)
        if wait > 0:
            raise HTTPException(status_code=429, detail=LOCKOUT_DETAIL,
                                headers={"Retry-After": RateLimiter.retry_after(wait)})
        return key

    def _pair_attempt(req: Request) -> None:
        """10/min per IP and 100/hour globally: every /pair attempt counts, valid or not."""
        ok_ip = limiter.hit(f"pair:ip:{_client_ip(req)}", 10, 60)
        ok_all = limiter.hit("pair:global", 100, 3600)
        if not (ok_ip and ok_all):
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
        ceiling = (body or {}).get("mode_ceiling")
        if ceiling is not None:
            try:
                ceiling = parse_mode(ceiling)  # a stored ceiling is read on every /status and /video
            except ModeError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        with app.state.db_session() as s:
            code = store_repo.create_pairing_code(s, household_id=ctx.household_id, kind=kind,
                                                  created_by_viewer=ctx.viewer_id,
                                                  mode_ceiling=ceiling,
                                                  viewer_label=(body or {}).get("label"))
            s.commit()
        return {"code": code, "expires_in": int(store_repo.PAIRING_TTL_S), "qr": _pair_qr(ctx.request, code)}

    _cached_offline_stations: dict[str, tuple[float, list[dict]]] = {}

    def _build_stations_list(lr, household_id: str, mode_ceiling: str = "full") -> list[dict]:
        stations_list = []
        seen_sids = set()
        if lr is not None:
            for s_id, c_obj in lr.channels.items():
                if s_id == "default" and len(lr.channels) > 1 and c_obj.runtime.phone is None:
                    continue
                seen_sids.add(s_id)
                sc = getattr(c_obj.pipeline, "station_cap", "full")
                eff_c = min_mode(sc, c_obj.policy.household_mode, mode_ceiling)
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
        if hasattr(app.state, "db_session") and app.state.db_session and household_id != LEGACY:
            now = time.monotonic()
            cached = _cached_offline_stations.get(household_id)
            if cached and (now - cached[0] < 10.0):
                db_stations = cached[1]
            else:
                db_stations = []
                with app.state.db_session() as s:
                    for db_st in store_repo.stations_of_household(s, household_id):
                        if db_st.revoked_at is None:
                            db_stations.append({
                                "id": db_st.id,
                                "name": db_st.name,
                                "dog_name": db_st.dog_name,
                            })
                _cached_offline_stations[household_id] = (now, db_stations)

            for db_st in db_stations:
                if db_st["id"] not in seen_sids:
                    seen_sids.add(db_st["id"])
                    default_dog = lr.runtime.cfg["web"].get("profile", {}).get("dog_name", "Your dog") if lr else "Your dog"
                    stations_list.append({
                        "id": db_st["id"],
                        "name": db_st["name"],
                        "dog_name": db_st["dog_name"] or default_dog,
                        "phone": None,
                        "pipeline_status": None,
                        "effective_mode": "full",
                        "battery": None,
                    })
        return stations_list

    @app.post("/pair")
    async def pair(req: Request, body: dict | None = None) -> dict:
        """Unauthenticated on purpose: the one-time code is the credential. Always the same 400 on
        failure, so a caller cannot tell a used code from a wrong one."""
        if not households_mode:
            _pin_only_404()
        lock_key = _lockout_key("pair", req)
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
                _cached_offline_stations.pop(consumed.household_id, None)
                lr = app.state.registry.peek(consumed.household_id)
                if lr is not None:
                    st_list = _build_stations_list(lr, consumed.household_id)
                    lr.hub.publish("status", {
                        "stations": st_list,
                        "paired_station": {"id": station.id, "name": station.name},
                    })
                limiter.record_success(lock_key)
                return {"station_id": station.id, "household_id": station.household_id,
                        "server_time": rt_clock()}
        except _BadPair:
            limiter.record_failure(lock_key)
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
            _cached_offline_stations.pop(ctx.household_id, None)
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
                ch.audio.kick_all(include_owners=True)  # the station is gone: its audio listeners leave too
                del lr.channels[station_id]
                if lr.default_channel_id == station_id:
                    lr.default_channel_id = next(iter(lr.channels.keys())) if lr.channels else None
            st_list = _build_stations_list(lr, ctx.household_id, getattr(ctx, "mode_ceiling", "full"))
            lr.hub.publish("status", {
                "stations": st_list,
                "revoked_station": {"id": station_id},
            })
        return {"ok": True}

    # -- viewer invites (T2.6): a code joins a viewer, the owner lists and revokes ------------------
    @app.post("/join")
    async def join(req: Request, body: dict | None = None) -> dict:
        """Unauthenticated: the one-time viewer code is the credential. A viewer never sees owner
        endpoints, but it may watch within its household."""
        if not households_mode:
            _pin_only_404()
        lock_key = _lockout_key("join", req)
        check_rate_limit("join", 2.0, f"ip:{_client_ip(req)}")
        code = str((body or {}).get("code") or "").strip()
        label = str((body or {}).get("label") or "viewer")[:64].strip()
        with app.state.db_session() as s:
            consumed = store_repo.consume_pairing_code(s, code, kind=KIND_VIEWER)
            if consumed is None:
                limiter.record_failure(lock_key)
                raise HTTPException(status_code=400, detail="invalid_or_expired_code")
            household_id, ceiling = consumed.household_id, (consumed.mode_ceiling or "full")
            viewer, token = store_repo.create_viewer(
                s, household_id=household_id, role=ROLE_VIEWER, label=label, mode_ceiling=ceiling)
            s.commit()
        limiter.record_success(lock_key)
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
        _viewer_cache.clear()
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
        except Exception:  # timeout, bad JSON, or a binary frame (receive_text raises KeyError): all fail the auth
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
        if not limiter.hit(f"create:{_client_ip(req)}", create_limit, 3600.0, count_denied=False):
            raise HTTPException(status_code=429, detail="too many households from this address; try again later")

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
        lock_key = _lockout_key("recover", req)
        check_rate_limit("recover", 5.0, f"ip:{_client_ip(req)}")
        key = str((body or {}).get("recovery_key") or "").strip()
        with app.state.db_session() as s:
            found = store_repo.recover_household(s, key)
            if found is None:
                limiter.record_failure(lock_key)
                raise HTTPException(status_code=401, detail="unknown recovery key")
            viewer, token = found
            s.commit()
        limiter.record_success(lock_key)
        resp = JSONResponse({"ok": True})
        _set_session_cookie(resp, token, req)
        return resp

    @app.post("/auth/logout")
    async def logout(req: Request) -> dict:
        if not households_mode:
            _pin_only_404()
        token = _viewer_token(req)
        if token:
            _viewer_cache.pop(token, None)
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
        if ctx.role != ROLE_OWNER:
            raise HTTPException(status_code=403, detail="only the owner can change the dog profile")
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

    # -- admin: live bandwidth tuning (no redeploy). Operator-only: the API token, never a household role.
    def _require_admin(req: Request) -> None:
        if not api_token:
            raise HTTPException(status_code=403, detail="admin API disabled: set BTB_API_TOKEN")
        auth_hdr = req.headers.get("Authorization", "")
        presented = auth_hdr[len("Bearer "):].strip() if auth_hdr.startswith("Bearer ") else req.headers.get("X-BTB-Token", "").strip()
        if not secrets.compare_digest(presented.encode(), api_token.encode()):
            raise HTTPException(status_code=401, detail="admin token required")

    @app.get("/admin/tuning")
    async def get_admin_tuning(request: Request) -> dict:
        _require_admin(request)
        return tuning.snapshot()

    @app.patch("/admin/tuning")
    async def patch_admin_tuning(request: Request, body: dict | None = None) -> dict:
        """Change any of video_fps, ws_frame_fps, gzip_enabled, gzip_min_size. Validated as a whole;
        takes effect on the next frame or response and is kept across restarts."""
        _require_admin(request)
        try:
            return tuning.update(body or {})
        except TuningError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - a failed save must say so; nothing was applied
            raise HTTPException(status_code=503, detail=f"could not save the settings: {exc.__class__.__name__}")

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
            viewer_ids = [v.id for v in store_repo.viewers_of_household(s, ctx.household_id)]
            store_repo.delete_household(s, ctx.household_id)
            s.commit()
        # Every open dashboard of the erased household gets a 4410 close (KICKED sentinel), not a
        # socket left waiting on a hub that will never publish again.
        for vid in viewer_ids:
            ctx.hub.kick(vid)
        await app.state.registry.forget(ctx.household_id)
        # Erase only this household's event-log directories: <root>/<hid> and <root>/<hid>_<station>.
        # The root (web.log_dir) is shared by every household and holds the database in some setups, so
        # it is never taken from a runtime's config (a station channel's config carries the root).
        import shutil

        root = Path(str(app.state.registry.cfg["web"]["log_dir"]))
        if ctx.household_id and ctx.household_id != LEGACY:
            for hid_dir in [root / ctx.household_id, *root.glob(f"{ctx.household_id}_*")]:
                if hid_dir.is_dir():
                    shutil.rmtree(hid_dir, ignore_errors=True)
        return {"deleted": True}

    rate_limit_cfg = web.get("rate_limits") or {}
    rate_limits_enabled = bool(rate_limit_cfg.get("enabled", auth_enabled))

    def check_rate_limit(action: str, min_interval_s: float, hid: str = LEGACY) -> None:
        if not rate_limits_enabled:
            return
        key = f"{hid}:{action}"  # one household's burst never limits another's
        wait_s = limiter.check_interval(key, min_interval_s)
        if wait_s > 0:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded for {action}. Please wait {wait_s}s before retrying."
            )

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
        import os
        is_fallback = bool(os.getenv("BTB_IS_FALLBACK", "0") in ("1", "true", "True"))
        if is_fallback:
            return {"ok": True, "fallback": True, "server": "render"}
        return {"ok": True}

    @app.get("/version")
    async def version() -> dict:
        import os
        is_fallback = bool(os.getenv("BTB_IS_FALLBACK", "0") in ("1", "true", "True"))
        return {
            "build_id": getattr(app.state, "frontend_build_id", ""),
            "server_origin": "render" if is_fallback else "self-hosted",
            "fallback": is_fallback,
        }

    @app.get("/status")
    async def status(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        import os
        is_fallback = bool(os.getenv("BTB_IS_FALLBACK", "0") in ("1", "true", "True"))
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        if station:
            ch = _resolve_channel(lr, station, ctx.household_id)
            if ch is None:
                return {
                    "phone": None, "state": "idle", "effective_mode": "full",
                    "station_cap": "full", "privacy_mode": False,
                    "pipeline_status": None, "max_idle_s": 180.0,
                    "viewers": {"video": 0, "audio": 0},
                    "fallback": is_fallback,
                    "server_origin": "render" if is_fallback else "self-hosted",
                    "build_id": getattr(app.state, "frontend_build_id", ""),
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
            st["fallback"] = is_fallback
            st["server_origin"] = "render" if is_fallback else "self-hosted"
            st["build_id"] = getattr(app.state, "frontend_build_id", "")
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

        st["stations"] = _build_stations_list(lr, ctx.household_id, getattr(ctx, "mode_ceiling", "full"))
        st["fallback"] = is_fallback
        st["server_origin"] = "render" if is_fallback else "self-hosted"
        st["build_id"] = getattr(app.state, "frontend_build_id", "")
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

    @app.get("/diary/daily")
    async def get_daily_diary(ctx: Ctx = Depends(auth_ctx), date: str | None = None, station: str | None = None) -> dict:
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        if station:
            ch = _resolve_channel(lr, station, ctx.household_id)
            timeline = ch.runtime.timeline() if ch else []
            treats = list(getattr(ch.pipeline, "_treats", [])) if ch else []
        else:
            timeline = ctx.runtime.timeline()
            treats = list(getattr(ctx.pipeline, "_treats", []))

        dog_name = "Your dog"
        if households_mode:
            with app.state.db_session() as session:
                hh = store_repo.get_household(session, ctx.household_id)
                if hh and hh.dog_name:
                    dog_name = hh.dog_name
        else:
            dog_name = cfg["web"].get("profile", {}).get("dog_name", "Your dog")

        from backend.analytics.diary import build_daily_diary
        return build_daily_diary(timeline, treats=treats, dog_name=dog_name, target_date=date)

    @app.post("/treat")
    async def treat(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        check_rate_limit("treat", 3.5, ctx.household_id)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        if target_station and ch is None:
            raise HTTPException(404, f"station {target_station!r} is not active")
        rt = ch.runtime if ch else ctx.runtime
        return {"ts": rt.treat()}

    # -- audio snippet uploads (0x5B: the phone's 5s bioacoustic WAVs for timeline replay) -----------
    snippet_cfg = web.get("snippets") or {}
    SNIPPET_MAX_BYTES = 1_000_000  # keep in step with the original hard cap (401: 1 MB)
    SNIPPET_MIN_BYTES = 44  # a bare RIFF/WAVE header
    SNIPPET_MAX_PER_HOUSEHOLD = int(snippet_cfg.get("max_per_household", 50) or 50)
    SNIPPET_RETENTION_S = float(snippet_cfg.get("retention_days", 7) or 7) * 86_400.0

    def _load_snippet_viewer(token: str):
        with app.state.db_session() as s:
            return store_repo.viewer_by_token(s, token)

    def _load_snippet_station(station_id: str):
        """A paired, unrevoked station row, or None for anything else (no oracle beyond 401)."""
        with app.state.db_session() as s:
            st = store_repo.get_station(s, station_id)
        return st if (st is not None and st.revoked_at is None) else None

    async def _snippet_household(request: Request) -> str:
        """Which household's directory a snippet upload may write into, or an HTTP refusal.

        Households mode honours the two credentials that already exist: a dashboard session
        (cookie or Bearer, T2.3) or the uploading phone's paired `X-Station-ID` (T2.5: the phone
        carries the station id and signs `/ingest-events` with its Keystore key — it has no viewer
        session, so a live, unrevoked station row is the station-identity credential here).
        Legacy/pin mode keeps `verify_request_auth` exactly as it was."""
        if not households_mode:
            verify_request_auth(request)
            check_rate_limit("snippet", 1.0, LEGACY)
            return LEGACY
        token = _viewer_token(request)
        if token:
            viewer = await asyncio.to_thread(_load_snippet_viewer, token)
            if viewer is not None:
                station_id = request.headers.get("x-station-id") or ""
                if station_id.strip():
                    st = await asyncio.to_thread(_load_snippet_station, station_id.strip())
                    if st is None or st.household_id != viewer.household_id:
                        raise HTTPException(status_code=403, detail="station not found in this household")
                check_rate_limit("snippet", 1.0, viewer.household_id)
                return viewer.household_id
        station_id = (request.headers.get("x-station-id") or "").strip()
        if station_id:
            st = await asyncio.to_thread(_load_snippet_station, station_id)
            if st is not None:
                check_rate_limit("snippet", 1.0, st.household_id)
                return st.household_id
        raise HTTPException(status_code=401, detail="unauthorized: household session or paired station required")

    def _sweep_snippets(snippets_dir: Path) -> int:
        """Per-household retention, run inline on every upload (no background task): delete WAVs
        older than web.snippets.retention_days, then the oldest beyond web.snippets.max_per_household.
        Only this household's own directory is ever touched."""
        deleted = 0
        try:
            files = [p for p in snippets_dir.iterdir() if p.is_file() and p.suffix == ".wav"]
            cutoff = time.time() - SNIPPET_RETENTION_S
            keep = []
            for p in files:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
                    deleted += 1
                else:
                    keep.append(p)
            excess = len(keep) - SNIPPET_MAX_PER_HOUSEHOLD
            if excess > 0:
                for p in sorted(keep, key=lambda p: p.stat().st_mtime)[:excess]:
                    p.unlink()
                    deleted += 1
        except OSError:
            return deleted
        return deleted

    @app.post("/events/audio-snippet")
    async def upload_audio_snippet(request: Request) -> dict:
        # Decide auth (and the destination directory) BEFORE reading any body bytes.
        household_id = await _snippet_household(request)
        snippet_id = request.headers.get("x-snippet-id")
        if not snippet_id:
            raise HTTPException(400, "Missing X-Snippet-ID header")
        safe_snippet_id = "".join(c for c in snippet_id if c.isalnum() or c in ("-", "_"))
        if not safe_snippet_id:
            raise HTTPException(400, "Invalid snippet ID")

        # Refuse oversize uploads without buffering the body: the declared size is checked first,
        # then the stream is consumed chunk by chunk and abandoned past the cap.
        declared = (request.headers.get("content-length") or "").strip()
        if declared.isdigit() and int(declared) > SNIPPET_MAX_BYTES:
            raise HTTPException(413, "Snippet audio payload exceeds maximum size")
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            if not chunk:
                continue
            total += len(chunk)
            if total > SNIPPET_MAX_BYTES:
                raise HTTPException(413, "Snippet audio payload exceeds maximum size")
            chunks.append(chunk)
        body = b"".join(chunks)
        if len(body) < SNIPPET_MIN_BYTES or body[:4] != b"RIFF":
            raise HTTPException(400, "Invalid WAV payload")

        snippets_dir = Path("out/snippets") / household_id
        snippets_dir.mkdir(parents=True, exist_ok=True)
        out_file = snippets_dir / f"{safe_snippet_id}.wav"
        out_file.write_bytes(body)
        _sweep_snippets(snippets_dir)
        return {"ok": True, "snippet_id": safe_snippet_id, "size": len(body)}

    @app.get("/snippets/{snippet_id}")
    async def get_audio_snippet(snippet_id: str, request: Request, ctx: Ctx = Depends(auth_ctx)) -> FileResponse:
        """Auth-cached household audio. The shared `out/snippets/legacy` fallback exists only in
        pin mode: in households mode a snippet outside THIS household's directory does not exist
        (T2.7 — one household's WAVs are never served to another's viewer)."""
        safe_snippet_id = "".join(c for c in snippet_id if c.isalnum() or c in ("-", "_"))
        snippets_dir = Path("out/snippets") / ctx.household_id
        file_path = snippets_dir / f"{safe_snippet_id}.wav"
        if not file_path.exists():
            if households_mode:
                raise HTTPException(404, "Audio snippet not found")
            legacy_file = Path("out/snippets") / LEGACY / f"{safe_snippet_id}.wav"
            if legacy_file.exists():
                file_path = legacy_file
            else:
                raise HTTPException(404, "Audio snippet not found")
        return FileResponse(file_path, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/torch")
    async def set_torch(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        check_rate_limit("torch", 1.2, ctx.household_id)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        if target_station and ch is None:
            raise HTTPException(404, f"station {target_station!r} is not active")
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
        if station and ch is None:
            raise HTTPException(404, f"station {station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        get_fn = getattr(pipeline, "get_torch", None)
        enabled = False if get_fn is None else bool(get_fn())
        return {"enabled": enabled}

    @app.post("/cue")
    async def trigger_cue(ctx: Ctx = Depends(auth_ctx), station: str | None = None, body: dict | None = None) -> dict:
        check_rate_limit("cue", 2.0, ctx.household_id)
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        target_station = station or (body.get("station") if isinstance(body, dict) else None)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        if target_station and ch is None:
            raise HTTPException(404, f"station {target_station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        fn = getattr(pipeline, "send_cue", None)
        if fn is None:
            raise HTTPException(400, "web.pipeline must support 'send_cue'")
        sound = body.get("sound", "squeak") if isinstance(body, dict) else "squeak"
        light = body.get("light") if isinstance(body, dict) else None
        sent = bool(fn(sound=sound, light=light))
        return {"ok": True, "sound": sound, "light": light, "sent": sent}

    @app.post("/voice/chunk")
    async def send_voice_chunk(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        if not isinstance(body, dict) or "pcm" not in body:
            raise HTTPException(400, "Missing 'pcm' base64 audio data")
        pcm_base64 = str(body["pcm"])
        if len(pcm_base64) > 350_000:
            raise HTTPException(413, "Voice chunk exceeds maximum size")
        target_station = body.get("station")
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, target_station, ctx.household_id)
        if target_station and ch is None:
            raise HTTPException(404, f"station {target_station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        fn = getattr(pipeline, "send_voice", None)
        if fn is None:
            raise HTTPException(400, "station pipeline does not support 'send_voice'")
        sent = bool(fn(pcm_base64))
        return {"ok": True, "sent": sent}

    @app.post("/telegram/link-code")
    async def create_telegram_link_code(ctx: Ctx = Depends(auth_ctx)) -> dict:
        check_rate_limit("tg_link", 1.0, ctx.household_id)
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg["web"].get("notify", {}).get("telegram", {}).get("token", "")
        bot_username = os.environ.get("TELEGRAM_BOT_USERNAME") or cfg["web"].get("notify", {}).get("telegram", {}).get("username", "WagWatchBot")
        with app.state.db_session() as session:
            if ctx.household_id == LEGACY:
                hh = store_repo.get_household(session, LEGACY)
                if not hh:
                    hh = Household(
                        id=LEGACY,
                        name="Default Household",
                        dog_name=cfg["web"].get("profile", {}).get("dog_name", "Your dog"),
                        location="",
                        recovery_hash="legacy",
                        llm_enabled=False,
                        log_enabled=False,
                        created_at=time.time(),
                        deleted_at=None,
                    )
                    session.add(hh)
                    session.flush()

            code = store_repo.create_pairing_code(session, household_id=ctx.household_id, kind="telegram", ttl_s=600.0)
            session.commit()
        deep_link = f"https://t.me/{bot_username}?start=link_{code}"
        return {
            "ok": True,
            "code": code,
            "link": deep_link,
            "bot_username": bot_username,
            "has_token": bool(bot_token),
            "expires_in_s": 600,
        }

    @app.get("/telegram/subscribers")
    async def get_telegram_subscribers(ctx: Ctx = Depends(auth_ctx)) -> dict:
        with app.state.db_session() as session:
            subs = store_repo.list_telegram_subscribers(session, ctx.household_id)
            return {
                "ok": True,
                "subscribers": [
                    {
                        "id": s.id,
                        "chat_id": s.chat_id,
                        "chat_type": s.chat_type,
                        "title": s.title,
                        "label": s.label,
                        "created_at": s.created_at,
                    }
                    for s in subs
                ],
            }

    @app.delete("/telegram/subscribers/{subscriber_id}")
    async def delete_telegram_subscriber(subscriber_id: str, ctx: Ctx = Depends(auth_ctx)) -> dict:
        with app.state.db_session() as session:
            ok = store_repo.remove_telegram_subscriber(session, ctx.household_id, subscriber_id)
            session.commit()
            if not ok:
                raise HTTPException(404, "Subscriber not found")
        return {"ok": True}

    @app.post("/telegram/webhook", response_model=None)
    async def telegram_webhook(request: Request):
        """Telegram's update sink. Public URL, so it is self-defending: hard rate limit, then a
        constant-time check of `X-Telegram-Bot-Api-Secret-Token` (web.notify.telegram.webhook_secret,
        the token `setWebhook` accepts), never a parse of an unauthenticated body."""
        ok = limiter.hit(f"tg_webhook:{_client_ip(request)}", 60, 60)
        if not ok:
            raise HTTPException(status_code=429, detail="too many webhook calls; slow down")
        if tg_webhook_secret:
            presented = (request.headers.get("x-telegram-bot-api-secret-token") or "").strip()
            if not secrets.compare_digest(presented.encode(), tg_webhook_secret.encode()):
                return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=403)
        body = await request.json()
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg["web"].get("notify", {}).get("telegram", {}).get("token", "")

        async def _tg_api(method: str, data: dict) -> dict:
            if not bot_token:
                return {}
            async with httpx.AsyncClient(timeout=8.0) as client:
                try:
                    r = await client.post(f"https://api.telegram.org/bot{bot_token}/{method}", json=data)
                    return r.json() if r.status_code == 200 else {}
                except Exception:
                    return {}

        def _household_exists(hid: str) -> bool:
            """A callback carries a household id that must be a real DB row: never a crafted id that
            would lazily materialise a HouseholdRuntime (and keep it) inside the registry."""
            with app.state.db_session() as s:
                return store_repo.get_household(s, hid) is not None

        # 1. Handle incoming text commands (/start link_..., /connect link_...)
        msg = body.get("message")
        if isinstance(msg, dict):
            text = (msg.get("text") or "").strip()
            chat = msg.get("chat", {})
            chat_id = str(chat.get("id"))
            chat_type = chat.get("type", "private")
            chat_title = chat.get("title") or ""
            from_user = msg.get("from", {})
            user_label = from_user.get("first_name") or from_user.get("username") or ""

            token_candidate = None
            if text.startswith("/start"):
                parts = text.split(maxsplit=1)
                if len(parts) > 1:
                    token_candidate = parts[1].removeprefix("link_").strip()
            elif text.startswith("/connect"):
                parts = text.split(maxsplit=1)
                if len(parts) > 1:
                    token_candidate = parts[1].removeprefix("link_").strip()

            if token_candidate:
                with app.state.db_session() as session:
                    pair = store_repo.consume_pairing_code(session, token_candidate, kind="telegram")
                    if pair:
                        store_repo.add_telegram_subscriber(
                            session,
                            pair.household_id,
                            chat_id,
                            chat_type=chat_type,
                            title=chat_title,
                            label=user_label,
                        )
                        hh = store_repo.get_household(session, pair.household_id)
                        dog_name = hh.dog_name if hh else "Your dog"
                        session.commit()
                        await _tg_api("sendMessage", {
                            "chat_id": chat_id,
                            "text": f"🎉 Connected to 🐾 *{dog_name}'s WagWatch*!\n\nYou will receive real-time snapshots, bark alerts, and instant remote comfort buttons in this chat.",
                            "parse_mode": "Markdown",
                        })
                        return {"ok": True, "connected": True}
                    else:
                        await _tg_api("sendMessage", {
                            "chat_id": chat_id,
                            "text": "⚠️ This WagWatch connection link has expired or was already used. Please generate a new link in WagWatch Settings.",
                        })
                        return {"ok": True, "error": "expired"}

        # 2. Handle interactive button callbacks (treat, cue)
        cb = body.get("callback_query")
        if isinstance(cb, dict):
            cb_id = cb.get("id")
            cb_data = (cb.get("data") or "").strip()
            cb_msg = cb.get("message", {})
            cb_chat_id = cb_msg.get("chat", {}).get("id")
            cb_msg_id = cb_msg.get("message_id")
            from_user = cb.get("from", {})
            user_name = from_user.get("first_name") or from_user.get("username") or "Pet Parent"

            if cb_data.startswith("treat:"):
                hid = cb_data.removeprefix("treat:").strip()[:64]
                if not hid or not await asyncio.to_thread(_household_exists, hid):
                    return JSONResponse({"ok": False, "error": "unknown_household"}, status_code=404)
                lr = app.state.registry.peek(hid) or await app.state.registry.get(hid)
                rt = lr.runtime if lr else None
                if rt:
                    rt.treat()
                await _tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": "🍖 Treat dropped for dog!"})
                cur_caption = cb_msg.get("caption") or cb_msg.get("text") or "🐾 Dog update"
                updated_cap = cur_caption + f"\n\n🍖 Treat dropped by {user_name} just now!"
                if "caption" in cb_msg:
                    await _tg_api("editMessageCaption", {
                        "chat_id": cb_chat_id,
                        "message_id": cb_msg_id,
                        "caption": updated_cap,
                    })
                else:
                    await _tg_api("editMessageText", {
                        "chat_id": cb_chat_id,
                        "message_id": cb_msg_id,
                        "text": updated_cap,
                    })
                return {"ok": True, "action": "treat"}

            elif cb_data.startswith("cue:"):
                parts = cb_data.split(":")
                sound = parts[1] if len(parts) > 1 else "squeak"
                hid = (parts[2] if len(parts) > 2 else "default").strip()[:64]
                if not hid or not await asyncio.to_thread(_household_exists, hid):
                    return JSONResponse({"ok": False, "error": "unknown_household"}, status_code=404)
                lr = app.state.registry.peek(hid) or await app.state.registry.get(hid)
                ch = _resolve_channel(lr, None, hid) if lr else None
                pipeline = ch.pipeline if ch else getattr(lr, "pipeline", None)
                if pipeline and hasattr(pipeline, "send_cue"):
                    pipeline.send_cue(sound=sound)
                await _tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": f"🔔 {sound.capitalize()} sound played on station!"})
                return {"ok": True, "action": f"cue:{sound}"}

        return {"ok": True}

    @app.get("/profile")
    async def get_profile(ctx: Ctx = Depends(auth_ctx)) -> dict:
        """The pet profile every viewer sees: header, captions, toasts."""
        return dict(ctx.runtime.cfg["web"].get("profile") or {})

    @app.put("/profile")
    async def put_profile(ctx: Ctx = Depends(auth_ctx), body: dict | None = None) -> dict:
        """Save the viewer's pet name/location/zone label and broadcast it to every dashboard."""
        if households_mode:  # the shared profile.json is not per household; use PUT /households/me/profile
            raise HTTPException(404, "use PUT /households/me/profile")
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
        if target_station and ch is None:
            raise HTTPException(404, f"station {target_station!r} is not active")
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
        limited = ch.limited if ch else ctx.limited
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
            if limited is not None:
                limited.clear_peeks()  # blackout must never be lifted by a stale peek window
            set_policy_fn = getattr(pipeline, "set_policy", None)
            if set_policy_fn:
                set_policy_fn("blackout")
            if not target_station and lr:
                for s_ch in lr.channels.values():
                    s_ch.policy.household_mode = "blackout"
                    if s_ch.limited is not None:
                        s_ch.limited.clear_peeks()
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
                if limited is not None:
                    limited.clear_peeks()
                set_policy_fn = getattr(pipeline, "set_policy", None)
                if set_policy_fn:
                    set_policy_fn(restore_mode)
                if not target_station and lr:
                    for s_ch in lr.channels.values():
                        s_ch.policy.household_mode = restore_mode
                        if s_ch.limited is not None:
                            s_ch.limited.clear_peeks()
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
        if app.state.demo_r2 is not None:
            # The bytes come from R2 (free egress); this host only sends the redirect.
            return RedirectResponse(app.state.demo_r2.url_for(demo_object_key(cid)), status_code=302)
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
        # A peek window only ever lifted `limited`; in any other mode it must not raise the mode.
        is_peeking = bool(limited and h_mode == "limited" and limited.is_peeking(ctx.viewer_id or "owner"))
        base = "full" if is_peeking else h_mode
        effective = min_mode(station_cap, base, getattr(ctx, "mode_ceiling", "full"))
        return {
            "household_mode": h_mode,
            "station_cap": station_cap,
            "effective": effective,
        }

    @app.post("/peek")
    async def post_peek(ctx: Ctx = Depends(auth_ctx), station: str | None = None) -> dict:
        """T3.5: temporary 15s window raising effective mode to min(full, ceiling) — only in the
        documented contract (docs/privacy_architecture_and_threat_model.md): the household is in
        `limited` (the mode a peek lifts) and the station cap is `full`."""
        lr = app.state.registry.peek(ctx.household_id) or await app.state.registry.get(ctx.household_id)
        ch = _resolve_channel(lr, station, ctx.household_id)
        if station and ch is None:
            raise HTTPException(404, f"station {station!r} is not active")
        pipeline = ch.pipeline if ch else ctx.pipeline
        policy = ch.policy if ch else (ctx.policy or getattr(ctx.runtime, "policy", None))
        tracker = ch.tracker if ch else ctx.tracker
        limited = ch.limited if ch else ctx.limited

        household_mode = policy.household_mode if policy else "full"
        if household_mode != "limited":
            raise HTTPException(
                status_code=409,
                detail={"error": "household_mode", "message": f"Peek not allowed while household mode is {household_mode}"}
            )
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

        if ctx.role != ROLE_OWNER:
            raise HTTPException(403, "only the household owner can change the viewing mode")

        if raw_mode in ("live", "demo"):
            rt = ch.runtime if ch else ctx.runtime
            try:
                return {"mode": rt.set_mode(raw_mode)}
            except ValueError as exc:
                raise HTTPException(400, str(exc))

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
            if ch.limited is not None:
                ch.limited.clear_peeks()  # the household lifted/lowered the mode: end every peek
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
                if s_ch.limited is not None:
                    s_ch.limited.clear_peeks()  # the household lifted/lowered the mode: end every peek
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
        raw_token = _viewer_token(ctx.request)
        # never keep a session token itself as an in-memory key
        viewer_id = ctx.viewer_id or (hashlib.sha256(raw_token.encode()).hexdigest()[:16] if raw_token else "viewer")

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

        # A tab left open on a household with no camera must not spin forever: stop after this much
        # silence (also makes ?max_frames=N terminate when no frame ever arrives).
        idle_s = float(web["video"].get("idle_timeout_s", 30.0))

        stream_id = str(uuid.uuid4())
        ua = ctx.request.headers.get("user-agent", "") if ctx.request else ""
        label = parse_ua_label(ua)

        async def gen():
            ticks = 0
            last_frame_at = time.monotonic()
            last_digest: bytes | None = None  # the phone sends ~0.5-5 fps; re-sending an unchanged JPEG wastes egress
            # Register inside the generator: if the client drops before the first iteration the stream
            # never starts, and a registration made earlier would never be released (ghost viewer).
            if tracker:
                try:
                    tracker.enter_video(stream_id, viewer_id=ctx.viewer_id, label=label, view=view or "focus")
                except Exception:
                    return
            try:
                # max_frames bounds served ticks (a test knob), not bytes written: unchanged frames are skipped below.
                while max_frames <= 0 or ticks < max_frames:
                    if tracker and tracker.is_kicked():
                        return
                    station_cap = getattr(pipeline, "station_cap", "full") if pipeline else "full"
                    pol = policy or ctx.policy or getattr(ctx.runtime, "policy", None)
                    h_mode = pol.household_mode if pol else getattr(pipeline, "_policy_mode", "full") if pipeline else "full"
                    # A peek only ever lifted `limited`: in privacy/blackout/full it must not raise anything.
                    is_peeking = bool(limited and h_mode == "limited" and limited.is_peeking(ctx.viewer_id or "owner"))
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
                        ticks += 1
                        digest = hashlib.blake2b(jpeg, digest_size=16).digest()
                        if digest != last_digest:
                            last_digest = digest
                            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode() \
                                + b"\r\n\r\n" + jpeg + b"\r\n"
                        last_frame_at = time.monotonic()
                    elif time.monotonic() - last_frame_at > idle_s:
                        return
                    # Read per tick, so an admin change to video_fps applies to streams already open.
                    await asyncio.sleep(1.0 / float(tuning.get("video_fps")))
            finally:
                if tracker:
                    tracker.leave_video(stream_id)

        return StreamingResponse(
            gen(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "close",
            },
        )

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
        origin = (sock.headers.get("origin") or "").strip().rstrip("/")
        if origin and urlparse(origin).netloc not in {sock.headers.get("host", ""),
                                                      sock.headers.get("x-forwarded-host", "")} \
                and origin not in cors_origins:
            await sock.close(code=4403, reason="Forbidden origin")  # cross-site WebSocket hijack guard
            return
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
            status_data["stations"] = _build_stations_list(lr, viewer.household_id, viewer.mode_ceiling or "full")
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

    # -- live audio (T3.9, A3.4): GET /audio, Full mode only --------------------------------------
    AUDIO_CLOSE_NOT_FOUND = 4404  # the WebSocket has no HTTP status once the handshake is done
    AUDIO_CLOSE_FORBIDDEN = 4403
    AUDIO_CLOSE_MODE = 4412

    async def _close_quietly(sock: WebSocket, code: int, reason: str) -> None:
        try:
            await sock.close(code=code, reason=reason)
        except (RuntimeError, WebSocketDisconnect):
            pass  # the client already went away

    def _audio_hello(codec: int, station_id: str) -> str:
        return json.dumps({"type": "audio_hello", "codec": codec, "rate": 16000, "chunk_ms": 100,
                           "station": station_id})

    async def _admit_audio(sock: WebSocket, ch, *, ceiling: str, viewer_id: str | None, label: str,
                           owner: bool) -> None:
        """Checks that need the station and the viewer, then the relay. The socket is already accepted,
        so every refusal is a close with a code the dashboard can act on."""
        if mode_rank(ceiling) < mode_rank("full"):
            await _close_quietly(sock, AUDIO_CLOSE_FORBIDDEN, "audio needs full mode for this viewer")
            return
        if ch.tracker is not None and ch.tracker.is_kicked():
            await _close_quietly(sock, CLOSE_VIEWERS_KICKED, "viewers disconnected by station")
            return
        if not _audio_allowed(ch, ceiling):
            await _close_quietly(sock, AUDIO_CLOSE_FORBIDDEN, "audio is not permitted in the current mode")
            return
        await _relay_audio(sock, ch, ceiling=ceiling, viewer_id=viewer_id, label=label, owner=owner)

    def _audio_allowed(ch, ceiling: str) -> bool:
        """min(station cap, household mode, viewer ceiling) is full: the only state that carries audio."""
        cap = getattr(ch.pipeline, "station_cap", "full")
        return ch.policy.compute_effective(cap, ceiling) == "full"

    async def _relay_audio(sock: WebSocket, ch, *, ceiling: str, viewer_id: str | None, label: str,
                           owner: bool) -> None:
        """One listener on one station. Registration is inside this coroutine and `finally` releases it, so
        a client that drops before anything is sent never leaves a ghost listener (the /video lesson).
        A watcher task reads the socket so an abrupt client drop is seen even when no frame is flowing."""
        hub: AudioHub = ch.audio
        tracker = ch.tracker
        stream_id = str(uuid.uuid4())
        q = hub.subscribe(owner=owner)

        async def watch_client() -> None:
            try:
                while True:
                    msg = await sock.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
            except (RuntimeError, WebSocketDisconnect):
                pass
            finally:
                _put_drop_oldest(q, CLIENT_GONE)

        watcher = asyncio.create_task(watch_client())
        try:
            try:
                if tracker is not None:
                    tracker.enter_audio(stream_id, viewer_id=viewer_id, label=label)
            except ViewersKickedError:
                await _close_quietly(sock, CLOSE_VIEWERS_KICKED, "viewers disconnected by station")
                return
            sent_codec = hub.last_codec if hub.last_codec in (0, 1) else 1
            await sock.send_text(_audio_hello(sent_codec, ch.station_id))
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    item = None
                if item is CLIENT_GONE:
                    return
                if item is KICKED_AUDIO:
                    await _close_quietly(sock, CLOSE_VIEWERS_KICKED, "viewers disconnected by station")
                    return
                if not _audio_allowed(ch, ceiling):  # re-checked on every frame and at least once a second
                    await _close_quietly(sock, AUDIO_CLOSE_MODE, "audio not permitted in the current mode")
                    return
                if item is None:
                    continue
                if item[0] != sent_codec:  # the phone changed codec mid-stream: say so before the frames
                    sent_codec = item[0]
                    await sock.send_text(_audio_hello(sent_codec, ch.station_id))
                await sock.send_bytes(item)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            watcher.cancel()
            hub.unsubscribe(q)
            if tracker is not None:
                tracker.leave_audio(stream_id)

    async def _audio_households(sock: WebSocket) -> None:
        """Households mode: the same credential and Origin guard as /ws. There is no household parameter:
        the viewer's own household decides which stations exist for it."""
        token = (sock.cookies.get(VIEWER_COOKIE) or "").strip()
        if not token:
            hdr = sock.headers.get("Authorization", "")
            token = hdr[len("Bearer "):].strip() if hdr.startswith("Bearer ") else ""
        await sock.accept()
        origin = (sock.headers.get("origin") or "").strip().rstrip("/")
        if origin and urlparse(origin).netloc not in {sock.headers.get("host", ""),
                                                      sock.headers.get("x-forwarded-host", "")} \
                and origin not in cors_origins:
            await _close_quietly(sock, AUDIO_CLOSE_FORBIDDEN, "Forbidden origin")
            return
        if not token:
            await _close_quietly(sock, 4401, "Unauthorized: no session")
            return
        with app.state.db_session() as s:
            viewer = store_repo.viewer_by_token(s, token)
            if viewer is None:
                viewer_info = None
            else:
                viewer_info = (viewer.household_id, viewer.id, viewer.role, viewer.mode_ceiling or "full")
        if viewer_info is None:
            await _close_quietly(sock, 4401, "Unauthorized: session revoked or expired")
            return
        hid, vid, role, ceiling = viewer_info
        lr = await app.state.registry.get(hid)
        try:
            ch = _resolve_channel(lr, sock.query_params.get("station") or None, hid)
        except HTTPException:
            ch = None
        if ch is None:
            await _close_quietly(sock, AUDIO_CLOSE_NOT_FOUND, "station not found in this household")
            return
        label = role if role == ROLE_OWNER else parse_ua_label(sock.headers.get("user-agent", ""))
        await _admit_audio(sock, ch, ceiling=ceiling, viewer_id=vid, label=label,
                           owner=(role == ROLE_OWNER))

    @app.websocket("/audio")
    async def audio(sock: WebSocket) -> None:
        """Phone audio to a viewer (docs/AUDIO_WIRE.md): `audio_hello` text, then binary phone frames
        without the 0x02 byte. Full mode only. Close codes: 4401 unauthorized, 4403 forbidden (ceiling below
        full, or not full at connect), 4404 unknown station, 4411 kicked, 4412 mode dropped below full."""
        if households_mode:
            await _audio_households(sock)
            return
        if not verify_ws_auth(sock):
            await sock.close(code=4401, reason="Unauthorized")
            return
        await sock.accept()
        lr = app.state.registry.peek(LEGACY) or await app.state.registry.get(LEGACY)
        try:
            ch = _resolve_channel(lr, sock.query_params.get("station") or None, LEGACY)
        except HTTPException:
            ch = None
        if ch is None:
            await _close_quietly(sock, AUDIO_CLOSE_NOT_FOUND, "station not found")
            return
        label = parse_ua_label(sock.headers.get("user-agent", ""))
        await _admit_audio(sock, ch, ceiling="full", viewer_id=None, label=label, owner=False)

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

    def _serve_apk() -> FileResponse:
        apk_candidates = [
            frontend_out / "wagwatch.apk",
            frontend_out / "app-debug.apk",
            frontend_out / "download" / "WagWatch.apk",
            Path(__file__).resolve().parent.parent / "android" / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk",
        ]
        for candidate in apk_candidates:
            if candidate.is_file():
                return FileResponse(
                    path=str(candidate),
                    filename="WagWatch.apk",
                    media_type="application/vnd.android.package-archive",
                    headers={"Cache-Control": "no-cache, no-store, must-revalidate, max-age=0"},
                )
        raise HTTPException(status_code=404, detail="APK build not found")

    @app.get("/wagwatch.apk")
    async def get_wagwatch_apk() -> FileResponse:
        return _serve_apk()

    @app.get("/download/apk")
    async def get_download_apk() -> FileResponse:
        return _serve_apk()

    @app.get("/app-debug.apk")
    async def get_app_debug_apk() -> FileResponse:
        return _serve_apk()
    if frontend_out.is_dir():
        from fastapi.staticfiles import StaticFiles
        from starlette.types import Scope
        from starlette.responses import Response

        class CleanStaticFiles(StaticFiles):
            async def get_response(self, path: str, scope: Scope) -> Response:
                response = await super().get_response(path, scope)
                if response.status_code == 404:
                    clean_path = path.rstrip("/")
                    if not clean_path:
                        clean_path = "index"
                    html_path = clean_path + ".html"
                    full_path, stat_result = self.lookup_path(html_path)
                    if stat_result:
                        response = self.file_response(full_path, stat_result, scope)

                content_type = response.headers.get("content-type", "")
                norm_path = path.lstrip("/")
                if norm_path.startswith("_next/static/"):
                    response.headers["cache-control"] = "public, max-age=31536000, immutable"
                elif "text/html" in content_type or norm_path.endswith(".html") or not norm_path or "." not in norm_path:
                    response.headers["cache-control"] = "no-cache, no-store, must-revalidate, max-age=0"
                    response.headers["pragma"] = "no-cache"
                    response.headers["expires"] = "0"
                return response

        app.mount("/", CleanStaticFiles(directory=str(frontend_out), html=True), name="frontend")

    return app


app = create_app()
