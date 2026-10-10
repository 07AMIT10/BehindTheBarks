# WagWatch: Multi-Household Pairing, Viewing Modes & Dashboard Overhaul

Date: 2026-10-10 | Status: VALIDATED DRAFT v2 (awaiting approval) | Replaces draft v1 of the same file.

How to use this document: Section 1 is evidence (what was actually run or read, and what could NOT be verified). Section 2 corrects wrong assumptions in draft v1. Section 4 is a list of self-contained tasks (T-numbers) written so a smaller agent can execute one task without reading the rest. Do tasks in order inside a phase; phases are ordered by dependency.

## 0. Goals

1. Fix the "Reconnecting…" screen after tapping Stop Monitoring in the app.
2. Remove hard-coded Bruno/Kitchen; make the dashboard phone-friendly.
3. Anyone can install the APK and pair by QR with no Render access; strict isolation between households/dogs.
4. Multiple station phones in one dashboard.
5. Viewing modes: Full (video + audio), Privacy (events only), Limited.
6. Battery stats and a config-driven debug/resource panel.
7. Better video quality.

## 1. Validation log (evidence)

Test rig used: Samsung SM-E075F (Galaxy A07), Android 16 (API 36), arm64-v8a, Wi-Fi 192.168.1.3, USB debugging via `adb` (serial R9ZY80PKWTW), backend run locally with `WEB_PIPELINE=remote uvicorn backend.main:app --port 8000`, phone connected through `adb reverse tcp:8000 tcp:8000`.

### 1.1 Verified by running it

| # | Finding | How verified |
|---|---|---|
| V1 | Baselines are green: `pytest tests/web/` = 259 passed; `npm --prefix frontend test -- --run` = 73 passed (11 files); `npm --prefix frontend run lint` clean. | ran them |
| V2 | **Stop bug reproduced on the real phone and in the dashboard** (screenshot at 390x844: header "Connected", "DebugPhone · disconnected", banner "Reconnecting to camera", video "Reconnecting…", still shows "10 fps"). | tapped "Stop Monitoring" via `adb shell input tap`, headless Chrome screenshot |
| V3 | **Root cause is two bugs, not one.** (a) Android: `EventUploader.stop()` calls `webSocket.close(1000, ...)` and then immediately `webSocket.cancel()` (`EventUploader.kt:117-121`). The close frame never reaches the server: logcat shows `WebSocket failed: java.net.SocketException: Socket closed`, and the backend logs `phone disconnected: DebugPhone (code 1005)`. (b) Backend: `Runtime.phone_close` only treats code 1000 as a deliberate stop, so 1005 keeps a `_phone_prev` snapshot with `connected: false`; `phoneNotice()` maps that to "dropped" and shows "Reconnecting to camera" forever. | adb logcat + backend log |
| V4 | Even with a clean 1000 close (simulated client `/tmp/sim_stop.py`), `phone` becomes `null` but `pipeline_status.state` flips to `stalled` ~2 s later (RemotePipeline keeps `_preview_ts`), which `Dashboard.tsx:47-48` turns into `paused` -> "Reconnecting…". So the server also needs an explicit idle state. | simulated websocket client |
| V5 | A second phone is rejected with close code 4409 "Another phone is already streaming" while the first is connected. Single-slot confirmed. | simulated client while the real phone was connected |
| V6 | `adb shell am start-foreground-service` on `MonitorService` fails (`Requires permission com.btb.ondevice.permission.CONTROL_MONITOR`). The working way to drive the app from adb is an activity intent: `adb shell am start -n com.btb.ondevice/.ui.MainActivity -a com.btb.ondevice.action.START --es extra_server_url ws://127.0.0.1:8000/ingest-events --es extra_device_name DebugPhone`. The `ACTION_STOP` activity intent did NOT stop an already-running session started this way (UI state was not "monitoring"); tapping the button did. Use `uiautomator dump` + `input tap` for stop. | tried each |
| V7 | While monitoring on the phone (screen on): events ~10/s, preview ~1.4 fps, 0 dropped. `hello.models` is `{}` (the app sends empty models). | `/status` while connected |
| V8 | `render.yaml` sets `BTB_DASHBOARD_PIN: "1234"` and the GitHub repo `07AMIT10/BehindTheBarks` is **PUBLIC**. Setting that env var turns auth on (`settings.py:89-91`), so the production dashboard PIN is publicly known. | `gh repo view`, reading `settings.py` |

### 1.2 Verified by reading code (file:line)

- **Video quality**: `MonitorService.encodePreviewJpeg` downsizes to **320 px long edge**, JPEG **quality 60** (`MonitorService.kt:428-461`), sent at most every 500 ms (`MonitorService.kt:318`, `EventUploader.previewMinIntervalMs = 500`). Analysis camera is 640x480 @15 fps (`MonitorService.kt:271-275`). Backend `/video` re-streams at `web.video.fps: 8` but only repeats the newest JPEG. So "poor video" = 320 px / q60 / ~1.4-2 fps, not a network limit.
- **Privacy mode today** (`MonitorService.kt:278-290, 334-340`): the camera callback returns early (no frames/events) AND the mic callback returns early (no audio events), and a slate JPEG is sent at 1 Hz. So today's "Privacy" = total blackout, NOT "bark labels only". Also `/privacy` accepts `enabled:false` from any dashboard viewer (`main.py` set_privacy, `EventUploader` "privacy" downlink -> `setPrivacyMode(enabled)`), so **a remote viewer can switch the station camera back on**. This contradicts AGENTS.md's "station has absolute sovereignty".
- **AGENTS.md section 4 overclaims**: chime on viewer connect, "👁️ N Viewers Active", and the 180 s inactivity pause are described as invariants but are NOT implemented (grep for viewer/chime/180/inactiv finds only `playTreatChime` and unrelated rotation code). `docs/privacy_architecture_and_threat_model.md` section 4 correctly marks them "Planned". Fix AGENTS.md as part of T3.x.
- **Treat downlink bug**: server sends `{"type":"treat","data":{"ts":..., "offset_s":..}}` (`remote_pipeline.py mark_treat`), but the app reads `root["ts"]` (`EventUploader.kt handleServerText`), which is absent, so the treat timestamp silently falls back to the phone clock. Minor, fix while touching downlinks.
- **Hello is built by string interpolation** (`EventUploader.kt` onOpen): a device name containing `"` or `\` produces invalid JSON / injection. Must switch to a JSON builder before adding fields.
- **Pairing UX**: scanning a QR requires monitoring to be running (`MainActivity.startQrScan` calls `service.setScanningQr` only if `isMonitoring`). The QR/clipboard payload is `{url, token?, device?}` parsed by `QrDecoder.parsePairingText` (also accepts plain `ws(s)://`/`http(s)://` URLs).
- **Deep link risk**: `MainActivity` is `exported` with a BROWSABLE `wagwatch://connect` / `btb://connect` filter and `handleIntent` silently overwrites URL/token/device from query parameters (`MainActivity.kt:460-475`). Any web page can re-point a station at an attacker's server. Pairing v2 must ask for confirmation.
- **Manifest**: `usesCleartextTraffic="true"`, `allowBackup="false"`, `minSdk 26`, `targetSdk 35`, FGS types camera|microphone, `CONTROL_MONITOR` signatureOrSystem permission. Default ingest URL comes from `BTB_DEFAULT_INGEST_URL` / Gradle prop `btbDefaultIngestUrl` (fallback `ws://10.0.2.2:8000/...`).
- **Auth model**: one shared set `{dashboard_pin, api_token, ingest_token}` checked with `tok in allowed` (not constant-time); tokens travel in query strings (`?token=`, also for `/video` because `<img>` cannot set headers - `VideoPanel.tsx:174-176`) and are stored in `localStorage` (`useBackend.ts`). WebSocket `/ws` also uses `?token=`. Same-origin cookie auth works because FastAPI serves `frontend/out` itself (`main.py` StaticFiles mount).
- **Singletons**: `Runtime.phone`, one `FusionState`, one `Hub`, one `EventLog(web.log_dir = "out")` created in `create_app`'s lifespan. Everything reads `app.state.rt`. The event log writes to disk (`out/`), which on an ephemeral host is both lossy and a privacy consideration.
- **Hello parsing accepts extra keys** (`parse_remote_hello` does not use `extra=forbid`), so adding `station_id`/signature fields to the hello is backward compatible. Pydantic *event* contracts do forbid extras (AGENTS.md invariant 1).
- **Battery/thermal**: `AndroidThermalProvider.getBatteryPercent()/getThermalStatus()/getThermalHeadroom()` exist (`ThermalGovernor.kt:10-40`) and `Metrics.kt` has `battery_pct`, but nothing is uplinked to the backend. Phone-side `dumpsys battery` works for cross-checking (phone was 93%, USB-charging, 31.9 C).
- **Bruno/Kitchen occurrences** (must all change): `config.yaml:194-195`, `backend/web/settings.py:39`, `frontend/components/Dashboard.tsx` (fallback profile, "Kitchen"), tests `tests/web/test_settings.py:18`, `tests/web/test_notify.py:22-32`, `tests/web/test_runtime.py:153`, `frontend/lib/store.test.ts`, `frontend/lib/toasts.test.ts`, `frontend/lib/demo.test.ts`, `android/.../QrDecoderTest.kt` ("Kitchen-Cam" is just a sample device name, can stay), docs `docs/DEMO_RUNBOOK.md`.

### 1.3 Environment facts that block or affect work

- **No JDK on this machine** (`java` not found; `/tmp/opencode/jdk` referenced by AGENTS.md no longer exists). Android unit tests (the "136 tests") and APK builds could NOT be run. Android SDK exists at `~/Android/Sdk`. Task T0.1 installs JDK 17.
- **The APK on the phone is stale**: installed `com.btb.ondevice` versionName 0.1.0-scaffold, last updated 2026-10-04 12:23; the latest release APK in the repo was built 2026-10-08 21:49 and source HEAD is 2026-10-08. All phone observations above (e.g. the stop behaviour) come from the older build; the relevant `EventUploader.stop()` code in source is identical to what was observed, but reinstall before any acceptance test (T0.3).
- The `mcp__chrome-devtools` tools fail (`/opt/google/chrome/chrome` missing). Working alternative: `/home/amit/.local/bin/google-chrome --headless=new --no-sandbox --window-size=390,844 --virtual-time-budget=8000 --screenshot=out.png URL`. Playwright Chromium also exists in `~/.cache/ms-playwright`.
- Local dev recipe that works: backend `WEB_PIPELINE=remote .venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` (serves the built dashboard from `frontend/out`), `adb reverse tcp:8000 tcp:8000`, phone URL `ws://127.0.0.1:8000/ingest-events`. Rebuild the dashboard with `npm --prefix frontend run build` after frontend changes (static export to `frontend/out`).

### 1.4 NOT verified (treat as assumptions, with how to settle them)

| Claim | Status | How to settle |
|---|---|---|
| Android Keystore supports Ed25519 on this device/API range | Web search was inconclusive. | Decision: **use ECDSA P-256 only** (Keystore EC support is standard since API 23, minSdk is 26). Optionally probe Ed25519 on the A07 in a spike (T2.9) but do not depend on it. |
| Render free web service: ephemeral disk, spins down after ~15 min idle, 100 GB/month egress | Only secondary sources found (blogs). | Check Render dashboard/docs before choosing storage. Note: `.github/workflows/keep-alive.yml` pings every ~10 min, which defeats spin-down. |
| Render free Postgres lifetime | Sources conflict (30 vs 90 days). | Check Render docs; prefer an external free Postgres (Neon/Supabase/etc.) whose terms you verify. |
| OkHttp `close()` then `cancel()` never delivers the close frame | Empirically confirmed on this phone (code 1005, "Socket closed"); no doc found. | Fixed and re-tested in T1.1. |
| Android unit test count/health, release build, 16 KB zipalign | Not run (no JDK). | T0.1/T0.2. |
| Opus/AAC encoding for live audio, WebRTC feasibility on Render | Not researched. | Spike T3.7 before committing. |
| `/camera` browser page and `/ingest` legacy path still work | Not exercised. | Keep their tests green (`tests/web/test_ingest*.py`). |

## 2. Corrections to draft v1

1. Preview rate is ~1.4-2 fps at 320 px/q60 (not "1 fps at MonitorService.kt:280"; that line is the privacy slate).
2. Stop bug has two causes (V3); the "close code 1000 -> idle" fix alone would NOT have worked because the app never sends 1000 on the wire.
3. Privacy mode is currently a total blackout including audio events (V2/1.2); the "Privacy = bark labels only" tier is a NEW behaviour, not a rename.
4. Remote viewers can currently disable privacy mode; mode control must be redesigned (station is authoritative, viewers can only reduce exposure).
5. AGENTS.md privacy invariants are aspirational, not implemented.
6. Public repo + PIN 1234: rotate the production PIN immediately and move it to a Render secret (T1.0).
7. Ed25519 dropped in favour of ECDSA P-256.
8. Android work cannot start until a JDK is installed (T0.1) and the phone gets a fresh build (T0.3).

## 3. Architecture target (unchanged in shape)

```
Household H1 --+- Station S1 (phone) --+
               +- Station S2 (phone) --+--> Backend --> HouseholdRuntime(H1) --> Hub(H1) --> viewers of H1 only
Household H2 ----- Station S3 ----------+--> HouseholdRuntime(H2) --> Hub(H2) --> viewers of H2 only
```

Isolation rule: no code path may read/write events, frames, JPEGs or downlinks without a `household_id` that came from an authenticated credential (never from a client-supplied parameter).

## 4. Tasks (detailed specs live in `2026-10-10-multi-household/`)

Each task there has context with file:line references, ordered steps, named tests with assertions, phone/browser acceptance checks and a definition of done, so a smaller agent can execute one task at a time. Start with `2026-10-10-multi-household/README.md` (global rules, dev loop, dependency graph, hand-off template).

| Phase | File | Tasks | Summary |
|---|---|---|---|
| 0 Environment | `phase-0-1-env-and-quickfixes.md` | T0.1-T0.4 | JDK 17 + Android toolchain, baselines, fresh APK on the phone, scripted dev loop and simulated phone |
| 1 Quick fixes | `phase-0-1-env-and-quickfixes.md` | T1.0-T1.7 | rotate public PIN, real stop (`bye` + close), backend idle state, "Monitoring stopped" UI, neutral profile, treat parsing, mobile layout, first-run empty state |
| 2 Households | `phase-2-households-pairing.md` | T2.1-T2.10 | storage, registry refactor, sessions, ECDSA-signed station pairing by QR, viewer invites, isolation suite, tester distribution, tester privacy defaults, retention |
| 3 Modes | `phase-3-modes-and-antilurking.md` | T3.1-T3.9 | `policy` downlink, station-authoritative modes, ceilings, viewer chime/indicator/180 s watchdog, limited-mode blur, mode UI, audio spike and implementation |
| 4 Multi-station | `phase-4-5-6-grid-video-telemetry.md` | T4.1-T4.4 | one Runtime per station, tagged envelopes, grid UI, demand-based stream policy |
| 5 Video | same | T5.1-T5.4 | measure, decouple preview encode from inference, adaptive ladder, optional hi-res and WebRTC spikes |
| 6 Telemetry/debug | same | T6.1-T6.4 | battery/thermal/RTT uplink, battery UI, config-driven debug drawer, alerts |
| 7 Hardening | (this file, section 7) | - | E2EE for Full mode, audit log, TLS-only release builds |

Additional facts found while writing the task specs (all verified 2026-10-10, in addition to section 1):
- A server with **no phone ever connected** also reports `pipeline_status.state = "stalled"` and so shows "Reconnecting…" (RemotePipeline treats "never had a frame" as stalled after the grace period). First-run users would see that screen; fixed in T1.2/T1.3/T1.7.
- `Runtime.status().fps` is never aged out, so "10 fps" lingers after Stop (T1.2).
- Preview JPEGs are encoded **inside the inference task** (`exec.submitLatest`), which drops the whole frame, preview included, when inference is busy; each camera frame also allocates a fresh 1.2 MB array (`CameraSource.processImage`). Preview fps is therefore bounded by inference rate (T5.2).
- `Runtime._run_llm`/`_notify` send room snapshots to the configured LLM provider and Telegram/WhatsApp; for tester households this must default to off (T2.9).
- `.venv` has **no** `sqlalchemy`, `cryptography`, `psutil` and no `pip` (uv-managed: `uv pip install --python .venv/bin/python ...`); Pillow is installed locally but missing from `requirements-web.txt`, so the production Docker image falls back to a blank privacy slate image.
- The dashboard is same-origin when served by FastAPI, but `frontend/lib/config.ts` allows a `?backend=` override that makes it cross-origin; cookie auth will not work there, so Bearer stays as a dev fallback. `CORSMiddleware` is `allow_origins=["*"]`, incompatible with credentialed requests.
- Docker runs one uvicorn worker, so in-memory registries and rate limiters are valid; do not add workers.
- `EventUploader.TERMINAL_CLOSE_CODES = {1000, 4400, 4401, 4408, 4409}`: new close codes (4410 revoked) must be added there.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Public repo with a live PIN | T1.0 now |
| Android work blocked (no JDK) / stale APK on phone | T0.1, T0.3 |
| Refactor from singleton `Runtime` breaks tests | `legacy` household first; no behaviour change until T2.7 exists |
| Render free tier (ephemeral disk, spin-down, bandwidth, DB lifetime) unverified | verify in Render before D1; external Postgres via `BTB_DATABASE_URL` |
| Event log writes to disk (`out/`) | per-household dirs; make logging opt-in or redact before multi-tenant release |
| Live audio cost (battery/thermal/bandwidth) | T3.7 spike before building |
| Mode enforcement bypass | station-side enforcement + server ceiling + wire/counter verification (T3.8) |
| Deep-link / QR hijack of station config | T2.5 confirmation dialog, signed challenge in T2.4 |
| CLAUDE.md non-negotiable: the Android app may not change an event contract | all new messages (`bye`, `challenge/auth`, `policy`, `mode_request`, `mode_state`, `telemetry`, `viewer`) are control envelopes, not FrameEvent/AudioEvent/RulesLabel changes; document them in CLAUDE.md in the same commit and keep `/ingest`, `/camera` and the mock/real pipelines working |
| Tester households spend operator LLM quota and send room images to third parties | T2.9 defaults off |

## 6. Decisions needed from the user

- **D1 Persistence**: external Postgres vs paid Render disk vs SQLite with re-pairing after restarts (recommended: `BTB_DATABASE_URL` abstraction; SQLite locally, external Postgres on Render).
- **D2 Accounts**: anonymous households + recovery key (recommended) vs email/Google login.
- **D3 Live audio** in Full mode: yes/no (adds a new uplink type and cost).
- **D4 Video ceiling**: higher-res JPEG now, WebRTC later?
- **D5 Blackout**: keep today's total-blackout "Family Privacy" as a separate mode from the new events-only Privacy mode? (recommended: yes).

## 7. Suggested order and effort

Phase 0 (0.5 d) -> Phase 1 (1.5 d) -> Phase 2 (5 d) -> Phase 3 (4 d) -> Phase 4 (3 d) -> Phase 6 (2 d) -> Phase 5 (2-3 d) -> Phase 7 (optional). About 18-20 working days sequentially; Phases 0-2 alone deliver "share the APK safely".
