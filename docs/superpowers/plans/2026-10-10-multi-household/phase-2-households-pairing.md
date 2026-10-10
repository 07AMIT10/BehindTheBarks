# Phase 2: households, QR pairing, isolation

Read `README.md` first. Prerequisites: Phase 1 merged (T1.2 `idle` state, T1.1 real stop). Blocking user decisions: **D1 storage** and **D2 accounts** (parent plan section 6). Defaults used below if the user has not answered: SQLite behind a `BTB_DATABASE_URL` setting (Postgres-compatible), anonymous households with a recovery key.

## Design (read before any task)

**Entities**
- `household`: isolation boundary. One dog profile in v1 (`dog_name`, `location`); two dogs = two households, or later two stations with different labels (Phase 4).
- `viewer`: a login. `role` is `owner` or `viewer`. A viewer's session cookie value is a random 256-bit token; only its SHA-256 is stored. `mode_ceiling` (Phase 3) and `expires_at` live here.
- `station`: a paired phone. Holds the phone's **ECDSA P-256 public key** (SPKI DER). Decision: P-256, not Ed25519 (Keystore Ed25519 support was not verifiable; EC P-256 is available on every supported API level, minSdk 26).
- `pairing_code`: one-time, 128-bit, TTL 300 s, `kind` = `station` | `viewer`, stored hashed.

**Credential -> context rule**: every route obtains `ctx = Depends(auth_ctx)` -> `Ctx(household_id, role, viewer_id, runtime)`. The household id is derived **only** from the credential (cookie/bearer/station key). No route reads a household id from query/body/path.

**Auth modes** (`web.auth.mode`): `pin` (today's behaviour, default so nothing breaks: all traffic belongs to a single `legacy` household using the existing `dashboard_pin/api_token/ingest_token`) and `households` (new). Existing tests run in `pin` mode.

**Verified constraints that shape the design**
- `create_app` accepts injected `pipeline`, `interpreter`, `notifier` and many tests use `create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())`; `tests/web/test_ingest_events.py` reads `c.app.state.rt` (lines 347 and 699). Keep `app.state.rt` pointing at the legacy household runtime.
- The dashboard is served same-origin by FastAPI (`frontend/out` mounted at `/`), and `frontend/lib/config.ts` returns same-origin base on https and `http://<host>:8000` on http. The `?backend=` override makes it cross-origin; cookie auth will not work there, so the Bearer path must remain for that dev case.
- `main.py` adds `CORSMiddleware(allow_origins=["*"])`; browsers refuse credentialed requests to `*`. In households mode use same-origin only (no CORS) unless `web.cors_origins` is configured, in which case `allow_credentials=True` with that explicit list.
- `Runtime._run_llm` sends the latest preview JPEG to the configured LLM provider and `Runtime._notify` sends snapshots to Telegram/WhatsApp. For tester households this would spend the operator's API quota **and ship strangers' living-room images to a third party**. Default for non-legacy households: LLM disabled and `DashboardOnlyNotifier`.
- `Hub` -> `EventLog(web.log_dir="out")` writes every envelope (frame features, audio labels, emotion) to `out/session-*.jsonl`. Per household: separate directory, and logging off by default for non-legacy households.
- The Docker image runs a single uvicorn worker (`CMD ... uvicorn backend.main:app`), so an in-memory registry and rate limiter are valid. Do not add `--workers`.
- Render free instances (to be verified in the Render dashboard) have an ephemeral filesystem: SQLite state is lost on redeploy/restart. Stations then get `4401`; the app must handle "pair again" gracefully (T2.5).

---

## T2.1 Storage layer and dependencies
- **Files (new)**: `backend/store/__init__.py`, `backend/store/db.py`, `backend/store/models.py`, `backend/store/repo.py`, `tests/web/test_store.py`. **Edit**: `requirements-web.txt`.
- **Dependencies**: `sqlalchemy` (2.x) and `cryptography` are NOT installed in `.venv` and not in `requirements-web.txt` (verified by `import` on 2026-10-10; `psutil` is also missing, needed in T6.3). The venv is `uv`-managed and has **no `pip`**: install with `uv pip install --python .venv/bin/python sqlalchemy cryptography` (this is the command the header of `requirements-web.txt` documents), then pin the resolved versions in `requirements-web.txt` (`uv pip freeze --python .venv/bin/python | grep -iE "^(sqlalchemy|cryptography)=="`). Add `psycopg[binary]` only as a comment ("for Postgres"). Also add `pillow` to `requirements-web.txt`: it is installed locally (12.3.0) and `remote_pipeline._build_privacy_slate` uses it, but the Docker image installs only `requirements-web.txt`, so production silently falls back to the hard-coded blank JPEG slate today. Keep the Docker build working (`docker build` is optional; at least confirm `uv pip install -r requirements-web.txt` resolves on Python 3.12).
- **Schema** (SQLAlchemy 2.0 declarative; UUID4 hex string primary keys):
  - `households(id, name, dog_name, location, recovery_hash, llm_enabled bool default false, log_enabled bool default false, created_at, deleted_at null)`
  - `viewers(id, household_id fk, role, label, token_hash unique, mode_ceiling default 'full', expires_at null, revoked_at null, created_at, last_seen)`
  - `stations(id, household_id fk, name, dog_name null, pubkey_spki blob, created_at, revoked_at null, last_seen)`
  - `pairing_codes(code_hash pk, household_id fk, kind, expires_at, used_at null, created_by_viewer fk, mode_ceiling null, viewer_label null)`
  - index on `token_hash`, `(household_id)`, `pairing_codes.expires_at`.
- **API** in `repo.py` (plain functions taking a `Session`, no FastAPI imports): `create_household`, `get_household`, `create_viewer`, `viewer_by_token(token) -> Viewer|None` (hash lookup, checks `revoked_at`/`expires_at`), `revoke_viewer`, `create_pairing_code(...) -> plaintext code`, `consume_pairing_code(code, kind) -> PairingCode|None` (**atomic**: single `UPDATE ... WHERE code_hash=:h AND kind=:k AND used_at IS NULL AND expires_at > :now`; success iff rowcount == 1), `create_station`, `station_by_id`, `revoke_station`, `delete_household(hid)` (hard delete all rows).
- **Config**: `web.database_url` default `sqlite:///out/btb.db`, env `BTB_DATABASE_URL`; for SQLite enable `PRAGMA foreign_keys=ON` and WAL; create tables on startup (`Base.metadata.create_all`). No Alembic in v1; write a `schema_version` row and a comment on how to migrate.
- **Secret handling**: all tokens/codes are `secrets.token_urlsafe(32)` (>=256 bit) except pairing codes (`secrets.token_urlsafe(16)`, 128 bit); hash with `hashlib.sha256`; compare with `hmac.compare_digest`; never log plaintext.
- **Tests** (`tests/web/test_store.py`, in-memory `sqlite://`): code is single-use (second `consume` returns None); expired code fails (inject clock); kind mismatch fails; concurrent consume (two threads, assert exactly one wins); revoked/expired viewer returns None; deleting a household cascades; token plaintext never present in any column (query all text columns and assert the token string is absent).
- **DoD**: pytest green; `pip install -r requirements-web.txt` resolves.

## T2.2 HouseholdRegistry and the context dependency (no behaviour change)
- **Files**: new `backend/web/registry.py`, `backend/web/auth.py`; edit `backend/main.py`, `backend/web/settings.py` (new `auth.mode`, `database_url`, `households` defaults).
- **Steps**
  1. `HouseholdRuntime` dataclass: `household_id`, `runtime: Runtime`, `hub: Hub`, `pipeline`. `HouseholdRegistry(cfg, make_pipeline, make_interpreter, make_notifier)` with `async get(hid)` (lazy create, per-household deep-copied cfg whose `web.profile` is read from the DB row, `web.log_dir = <log_dir>/<hid>` and `EventLog` only if `log_enabled`), `peek(hid)`, `evict_idle(now)` (no station connected and no viewers for `web.registry.idle_evict_s`, default 600), `close()`. Each runtime is `await rt.start()`ed on creation and `await rt.stop()`ed on eviction/shutdown.
  2. The `legacy` household wraps the objects injected into `create_app` (or the default ones built exactly as today). `app.state.rt` and `app.state.hub` keep pointing at it so existing tests and `spy_runtime(c.app.state.rt)` still work.
  3. `auth.py`: `Ctx` dataclass and `auth_ctx(request_or_websocket)` returning `Ctx`. In `pin` mode it reproduces today's `verify_request_auth` / `verify_ws_auth` semantics and returns the legacy ctx. Replace the repeated `verify_request_auth(req)` calls in routes with `ctx: Ctx = Depends(auth_ctx)` and `ctx.runtime` instead of `app.state.rt` (routes: `/status /events /treat /torch /camera/flip /privacy /mode /demo/notify /phone/config /video /ws`; `/demo/clips*`, `/health`, `/auth/*` stay public).
  4. Rate limits (`check_rate_limit`) become per-household (key `f"{hid}:{action}"`).
  5. Do **not** add any new endpoint or schema usage yet; this task is a pure refactor.
- **Tests**: the whole existing suite must pass unchanged (259). Add `tests/web/test_registry.py`: legacy household identity (`app.state.rt is registry.peek("legacy").runtime`), two `get("a")` calls return the same object, eviction stops the runtime (`rt._stopped is True`) and a later `get("a")` creates a fresh one, per-household cfg is a deep copy (mutating H1 profile does not change H2).
- **DoD**: 259 existing + new tests pass; `git diff --stat` shows no frontend/android change.
- **Pitfalls**: `Runtime.start()` creates three asyncio tasks (pipeline, tick at `tick_hz=4`, phone-status loop); creating them lazily requires the running loop, so `get()` is `async`. `ws` routes call `app.state.hub.subscribe()`; switch to `ctx.hub`.

## T2.3 Household bootstrap, sessions, profile
- **Endpoints** (households mode only; in `pin` mode they return 404):
  - `POST /households` body `{name?, dog_name?, location?}` -> creates household, owner viewer, recovery key (`secrets.token_urlsafe(24)`, stored hashed); response `{household_id, recovery_key}` (**shown once**) and `Set-Cookie: btb_session=<token>; HttpOnly; SameSite=Lax; Path=/; Max-Age=31536000; Secure` (Secure only when `request.url.scheme == "https"` or `X-Forwarded-Proto: https`; the Dockerfile already passes `--proxy-headers`). Per-IP limit 5/hour (in-memory).
  - `POST /auth/recover` `{recovery_key}` -> new owner session cookie (rate limited; constant-time compare via hashes).
  - `GET /me` -> `{household_id, role, name, profile, mode_ceiling}`; 401 if no session.
  - `PUT /households/me/profile` (owner) `{dog_name, location}` (<=48 chars each, stripped) -> updates DB and the live `runtime.cfg["web"]["profile"]` dict, then `hub.publish("status", {"profile": ...})`.
  - `POST /auth/logout` clears the cookie (and revokes that viewer row if role viewer).
  - `DELETE /households/me` (owner, body `{recovery_key}`) -> `repo.delete_household`, evict runtime, close its sockets with code 4410, delete `<log_dir>/<hid>`; returns `{"deleted": true}`. This is the user's data-erasure path; test it.
- **Cookie vs header**: `auth_ctx` accepts the cookie; for the cross-origin dev case (`?backend=`) also accept `Authorization: Bearer <viewer token>`. Remove `?token=` for households mode (tokens in URLs leak into logs); `/video` and `/ws` work with the cookie because the dashboard is same-origin.
- **Frontend**
  - `frontend/lib/useBackend.ts`: add `fetch(..., {credentials: "include"})` for same-origin; on 401 in households mode show a **Welcome screen** (replaces `AuthModal` PIN gate when `/auth/status` returns `{mode: "households"}`; extend that endpoint to return `mode`). Welcome: "Create household" (dog name, optional location) and "I already have a recovery key". After creation show the recovery key with Copy/Download and a required "I saved it" checkbox before continuing.
  - Header: profile edit sheet (dog name, location) calling `PUT /households/me/profile`; sign out; delete household (typed confirmation).
  - Types: `Status.profile` unchanged; add `AuthStatus = {enabled: boolean; auth_required: boolean; mode: "pin" | "households"}`.
- **Tests**: `tests/web/test_households.py`: create -> cookie set, `/me` works, `/status` returns the household's profile; wrong recovery key 401; recovery works; rate limit 429 on the 6th create; profile update visible in `/status` and published on `/ws`; delete removes the DB rows and later requests with the old cookie get 401; logout clears. Vitest for the auth-status mapping and the welcome-screen reducer logic (pure functions only).
- **DoD**: suites green; manual: fresh browser profile -> Welcome -> create -> dashboard shows the chosen dog name (no "Bruno").

## T2.4 Station pairing v2 (backend)
- **Endpoints**
  - `POST /households/me/pairing` (owner) body `{kind:"station"}` -> `{code, expires_in: 300, qr: {v:2, url: "<wss base>/ingest-events", pair_url: "<https base>/pair", code}}` (server builds the URLs from `request.url`/forwarded headers so the QR matches the host the owner is using).
  - `POST /pair` body `{code, pubkey_spki_b64, device_name}` (unauthenticated; the code is the credential). Validate: base64 decodes to <=200 bytes; `cryptography.hazmat.primitives.serialization.load_der_public_key` yields an `EllipticCurvePublicKey` on curve `secp256r1`; name 1-64 chars. Atomically `consume_pairing_code`; on success create the station and return `{station_id, household_id, server_time}`. Failure always returns the same `400 {"error":"invalid_or_expired_code"}` (no oracle). Rate limit: 10/min per IP and a global 100 failures/hour counter -> 429.
- **Ingest handshake (`/ingest-events`)**
  1. Parse hello (`parse_remote_hello` already tolerates extra keys). New optional fields: `station_id` (str <=64). `proto` becomes `1` or `2`: extend the check in `parse_remote_hello` (`proto != PROTO` today) to accept 1 and 2, and add `station_id: str | None` to `RemoteHello`. In `pin` mode behaviour is unchanged.
  2. In households mode: `proto != 2` or unknown/revoked `station_id` -> `close(4401, "Unauthorized")`. Otherwise send `{"type":"challenge","nonce":"<b64 32 random bytes>","server_time":<secs>}` and wait up to 10 s for `{"type":"auth","sig":"<b64 DER ECDSA signature>"}`. The signed message is `b"btb-auth-v1|" + nonce_bytes + b"|" + station_id.encode() + b"|" + device.encode()`. Verify with `public_key.verify(sig, msg, ec.ECDSA(hashes.SHA256()))`; failure/timeout -> `close(4401)`. On success proceed exactly as today (`rt.phone_open`, `hello_ack`), using the station's household runtime (`registry.get(station.household_id)`), and update `stations.last_seen`.
  3. New close code `CLOSE_REVOKED = 4410` in `backend/web/ingest.py` (document next to the others). `DELETE /stations/{id}` (owner; id must belong to the caller's household else 404) sets `revoked_at` and closes the live socket with 4410 via the session's closer.
  4. Nonce replay: a nonce is generated per connection and discarded; nothing to store. Add `server_time` to the challenge so the app can later detect gross clock errors (informational).
- **Tests** (`tests/web/test_pairing.py`; helper builds an EC key with `cryptography`, signs the challenge, and drives `TestClient.websocket_connect("/ingest-events")`): happy path pairs then connects and receives `hello_ack`; reused code -> 400; expired code -> 400 (inject clock via cfg or monkeypatch `repo.now`); wrong key signature -> close 4401; signature for a different station id or different nonce -> 4401; revoked station -> 4401; revoke during a live session -> client receives 4410 and `rt.phone is None`; `proto:1` hello in households mode -> 4401; `pin` mode legacy hello still works (existing tests); malformed pubkey -> 400; rate limit 429.
- **DoD**: green; sequence documented in `CLAUDE.md` (protocol section) and in the docstring of `backend/web/ingest_events.py` (add the challenge/auth steps to the protocol list).

## T2.5 Station pairing v2 (Android)
- **Files**: new `android/app/src/main/java/com/btb/ondevice/net/Pairing.kt`, `.../net/StationKey.kt`; edit `EventUploader.kt`, `MainActivity.kt`, `MonitorService.kt`, `util/QrDecoder.kt`; tests `PairingTest.kt`, extend `EventUploaderTest.kt`, `QrDecoderTest.kt`.
- **Steps**
  1. `StationKey`: `getOrCreate(alias="btb_station_key")` using `KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")` with `KeyGenParameterSpec.Builder(alias, PURPOSE_SIGN).setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1")).setDigests(DIGEST_SHA256)` (try `setIsStrongBoxBacked(true)` first and fall back on `StrongBoxUnavailableException`); `publicSpkiB64()`, `sign(bytes) = Signature.getInstance("SHA256withECDSA")` (DER output, which matches the server verify). Wrap in an interface `Signer { fun sign(data: ByteArray): ByteArray; fun publicSpkiB64(): String }` so unit tests inject a software key (Keystore is unavailable in JVM unit tests).
  2. `Pairing.pair(baseHttpUrl, code, deviceName, signer): PairResult` using OkHttp (POST JSON to `/pair`), returns `station_id`, `household_id`; persist in `btb_station_prefs` keys `station_id`, `household_id`, `server_url`, `household_name`.
  3. `EventUploader`: new constructor params `stationId: String? = null`, `signer: Signer? = null`. When `stationId != null` the hello is `proto 2` with `station_id`; on `{"type":"challenge"}` reply `{"type":"auth","sig":"<b64>"}` signing `b"btb-auth-v1|"+nonce+b"|"+stationId+b"|"+deviceName` (same bytes as the server); only `hello_ack` flips to CONNECTED (already so). Add `CLOSE_REVOKED = 4410` to `TERMINAL_CLOSE_CODES`; `4401` and `4410` call a new listener method `onPairingInvalid()` so the UI can say "This station was removed. Pair again." and clear stored `station_id`.
  4. QR/paste: `QrDecoder.parsePairingText` accepts v2 JSON `{v:2,url,pair_url,code}` in addition to v1 and plain URLs; returns a sealed type `PairingConfig` (v1 manual config) or `PairingCode(pairUrl, wsUrl, code)`.
  5. **Pairing must work without starting monitoring.** `startQrScan` currently requires `isMonitoring` (it calls `monitorService.setScanningQr`). Add a standalone scan path that opens the camera just for scanning (CameraX `ImageAnalysis` reusing `QrDecoder.decodeRgba`), closes it when a code is read, and then starts pairing; the existing in-monitoring scan can stay.
  6. **Deep link safety**: in `MainActivity.handleIntent` the `wagwatch://connect?url=&token=&device=` handler currently overwrites prefs silently (`MainActivity.kt` ~460-475). Replace with a confirmation dialog showing the target host ("Connect this station to <host>?"); apply only on Confirm; ignore `token` unless the host equals the currently configured host. Intent extras `extra_server_url/extra_token/...` (used by the adb dev loop) must keep working only when `BuildConfig.DEBUG` is true.
  7. Settings area (existing MainActivity layout): show "Household: <name>" and an **Unpair** button (clears station prefs, stops monitoring).
  8. On `4401`/`4410`: stop retrying (already terminal), show the message above.
- **Tests**
  - `PairingTest`: with `MockWebServer` returning `{station_id, household_id}` assert request JSON fields (`code`, `pubkey_spki_b64`, `device_name`) and persistence; 400 response -> `PairResult.Error`.
  - `EventUploaderTest`: server sends `challenge`, test signer returns fixed bytes; assert the `auth` reply carries base64 of the signer output and the signed message equals `"btb-auth-v1|"+nonce+"|"+id+"|"+device`; 4410 terminates without reconnect and fires `onPairingInvalid`.
  - `QrDecoderTest`: v2 JSON, v1 JSON, plain URL, garbage -> null.
  - Keystore itself is verified on the phone: log the algorithm and `isInsideSecureHardware` via `KeyFactory`/`KeyInfo` once in debug.
- **Phone check**: pair against a local backend in households mode, start monitoring, see the station connect; revoke from the API and observe the app stop with the "removed" message.
- **DoD**: android tests green; APK builds and passes zipalign 16 KB check; phone check recorded.

## T2.6 Viewer invites and owner controls
- **Backend**: `POST /households/me/pairing {kind:"viewer", mode_ceiling:"full|limited|privacy", expires_in_s?, label?}` -> code; `POST /join {code, label}` (unauthenticated) consumes it, creates a `viewer` row and sets the cookie; `GET /households/me/members` lists stations (id, name, last_seen, revoked) and viewers (id, label, role, ceiling, last_seen); `DELETE /viewers/{id}` (owner; cannot delete self; closes that viewer's websockets with 4410 — track subscriptions by viewer id in `Hub.subscribe(viewer_id=...)`; add an optional `owner` tag to the queue registry and `Hub.kick(viewer_id)`).
- **Frontend**: rewrite `PairingModal.tsx` into tabs "Pair a phone" and "Invite a viewer": fetch a code, render the QR (`qrcode` package already used), 5-minute countdown with regenerate, copyable join link `https://<host>/?join=<code>` handled on load (calls `/join`). A "Members" sheet lists stations/viewers with Revoke.
- **Tests**: `tests/web/test_viewers.py`: join via code works once; expired/used code 400; viewer role cannot call owner endpoints (403); revoked viewer's cookie 401 and open `/ws` closes; ceiling is stored (enforced in Phase 3).
- **DoD**: green; manual: second browser profile joins as viewer via the link.

## T2.7 Isolation test suite (blocking gate for all later phases)
- **File**: `tests/web/test_isolation.py`. Reuse `scripts/sim_phone.py` ideas but as in-process `TestClient` websockets with a small `PairedStation` helper (generate key, pair, connect, send envelopes).
- **Assertions** (each its own test):
  1. Two households H1/H2 with one station each; H1 station sends frame/audio/rules + preview JPEG with a distinctive byte marker; H2 viewer's `/ws` (collect 3 s), `/events`, `/status`, `/video?max_frames=2` never contain H1's marker, device name, or emotion events; and vice-versa.
  2. A viewer cookie from H1 cannot be used on H2 resources: no route accepts a household id, so also try query/body/header spoof attempts (`?household_id=`, `X-Household`, JSON body) and assert they are ignored.
  3. H1 station revoked -> its socket closes 4410, H1 `/status.phone` becomes null, H2 unaffected.
  4. H1 `DELETE /households/me` removes H1 data; H2 data intact; evicted H1 runtime is gone from the registry.
  5. LLM/notifier isolation: with a fake interpreter registered globally, a non-legacy household never calls it (`llm_enabled=false` default) and no snapshot is sent to any external notifier.
  6. Event log isolation: with `log_enabled=true` for H1 only, files exist only under `<log_dir>/<H1>/`.
  7. `pin` mode regression: legacy single-household behaviour (existing tests) unchanged.
- **DoD**: all pass; mark this suite as required in `CLAUDE.md` and in the README global rules (re-run on every later task that touches `main.py`, `registry.py`, `hub.py`, `runtime.py` or `remote_pipeline.py`).

## T2.8 Tester distribution
- **Steps**: bake the default backend URL into release builds via `-PbtbDefaultIngestUrl=wss://<host>/ingest-events` (already supported in `android/app/build.gradle.kts`); document the signing key requirement (`BTB_KEYSTORE_PATH`, default `release.jks`; if absent the build silently falls back to the **debug** keystore, so the release script must fail loudly when `release.jks` is missing); update `docs/distribution/RELEASE_GUIDE.md` and `docs/USER_ONBOARDING_GUIDE.md` with the tester flow (install APK -> open dashboard URL -> Create household -> Pair a phone -> scan with the app). Build `assembleRelease`, run zipalign `-P 16`, record APK size and SHA-256 in the guide.
- **Do not** upload the APK or change the shared Drive link without the user's approval; if the download URL changes, regenerate `assets/wagwatch_apk_download_qr.png` and `wagwatch_apk_share_card.png` only after approval.
- **DoD**: a clean-phone walkthrough script in the guide (and, if possible, executed on the A07 after `adb uninstall` with user consent) that ends with a live feed in under 2 minutes.

## T2.9 Privacy defaults for tester households
- **Steps**: in the registry factory, non-legacy households get a disabled interpreter stub (a small class with `enabled = False` and `last_call = None`, like `NoLLM` in `tests/web/test_main.py`; `Runtime` only reads those two attributes when the interpreter is off) instead of `LLMInterpreter`, and `DashboardOnlyNotifier`, `log_enabled=false`; owner can opt in per household via `PUT /households/me/settings {llm_enabled, log_enabled}` with a clear warning in the UI ("sends a snapshot of your room to <provider>"). Show current LLM state in Settings.
- **Tests**: covered by T2.7 (5, 6) plus a settings round-trip test.

## T2.10 Retention and cleanup
- **Steps**: background task in the registry: delete `<log_dir>/<hid>/session-*.jsonl` older than `web.log_retention_days` (default 7); expire `pairing_codes` older than 1 day; mark viewers past `expires_at` revoked; evict idle runtimes. Unit-test with an injected clock and temp dirs.
- **DoD**: tests green; documented in `docs/privacy_architecture_and_threat_model.md` (what is stored, where, for how long).
