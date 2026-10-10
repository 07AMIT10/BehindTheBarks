# Phase 3: viewing modes and anti-lurking

Read `README.md` first. Prerequisites: Phase 2 (household context `ctx`, viewer `mode_ceiling`, station key auth) and the isolation suite (T2.7) green. Blocking user decisions: **D3** (live audio) for T3.9 only, **D5** (keep a separate total "Blackout" mode).

## Verified starting point (2026-10-10)

- Android "privacy" today (`MonitorService.kt`, state `privacyMode: AtomicBoolean`, `setPrivacyMode` at ~119): the camera callback returns early, sending only a slate JPEG once per second (`getOrCreatePrivacySlate`), so **no `FrameEvent`/rules are produced**; the mic callback (`MicSource` `audioCallback`, ~334-340) also returns early, so **no `AudioEvent`s** either. It is a total blackout.
- Anyone with dashboard access can switch it off: `POST /privacy {"enabled": false}` -> `RemotePipeline.set_privacy` -> downlink `{"type":"privacy","data":{"enabled":false}}` -> `EventUploader` `"privacy"` branch -> `listener.onPrivacyDownlink(false)` -> `MonitorService.setPrivacyMode(false)`. This violates "the station has absolute sovereignty" (AGENTS.md section 4).
- Preview video: JPEG encoded **inside the inference task** (`exec.submitLatest { ... encodePreviewJpeg ... up.sendPreview }`, `MonitorService.kt` ~305-326) at most every 500 ms (`lastPreviewSendMs`), 320 px long edge, JPEG quality 60; `EventUploader.sendPreview` adds its own 500 ms limit and a 256 KB queue backpressure drop. Because encoding runs after inference, effective preview fps (~1.4) is bounded by inference duration.
- The phone always encodes and uploads previews, even with zero viewers (battery/bandwidth cost). Backend `GET /video` just repeats the newest JPEG at `web.video.fps` (8) to each client with no viewer tracking.
- AGENTS.md section 4 claims the chime, viewer badge and 180 s watchdog exist; they do not (only `playTreatChime`). `docs/privacy_architecture_and_threat_model.md` section 4 lists them as "Planned".
- Server-side imaging libs available: `opencv-python-headless` and numpy are in `requirements-web.txt`; Pillow is used with a fallback in `remote_pipeline._build_privacy_slate` but is not in requirements.

## Mode model

| Mode | Preview video | Audio stream | Events (frame features, bark/audio labels, emotion, meals) | Notes |
|---|---|---|---|---|
| `full` | live, per policy | live if audio allowed (T3.9) | yes | |
| `limited` | blurred low-res still, at most one per 10 s, plus explicit "peek" (full frame for 15 s, chimes) | none | yes | |
| `privacy` | none | none | yes (keypoints are numbers, not images) | the new "bark and behaviour only" tier |
| `blackout` | none (server renders a slate) | none | none | today's "Family Privacy" |

Ordering for ceilings: `blackout < privacy < limited < full`. **Effective mode for a viewer** = `min(station_cap, household_policy, viewer.mode_ceiling)`. The **station cap** is the exposure the person holding the phone allows; it can only be raised on the device or through an on-device approval prompt. Reductions (to a lower mode) are applied immediately from anywhere.

Single control message server -> phone, replacing ad-hoc `privacy` downlinks:
```json
{"type":"policy","data":{"mode":"full","preview":{"fps":2,"max_edge":320,"quality":60},"audio":false,"viewers":{"video":1,"audio":0}}}
```
The phone enforces `min(local_cap, policy.mode)`, and sends previews only when `viewers.video > 0` (plus an optional low-rate keepalive snapshot when an external notifier/LLM is on). The server sends `policy` on connect and whenever viewers/mode change. The legacy `{"type":"privacy"}` downlink is still sent too (old APKs ignore `policy`; new APKs ignore `privacy` when they have seen a `policy`). Phone -> server status: `{"type":"mode_state","data":{"cap":"full","effective":"privacy","reason":"station"}}` whenever the cap changes.

---

## T3.1 Contracts and policy plumbing
- **Files**: `backend/web/remote_pipeline.py`, `backend/web/ingest_events.py`, `backend/contracts.py` (only if a pydantic model is warranted; the control messages are plain JSON like `torch`/`flip`/`config`), `frontend/lib/types.ts`, `android/.../net/EventUploader.kt`, `CLAUDE.md`.
- **Steps**
  1. Define in one place (`backend/web/modes.py`, new): `MODE_ORDER = ("blackout","privacy","limited","full")`, `min_mode(*modes)`, `parse_mode(str)`, `DEFAULT_PREVIEW = {"fps":2,"max_edge":320,"quality":60}`.
  2. `RemoteSession.handle_text`: accept `{"type":"mode_state","data":{cap,effective,reason}}`, store `session.station_cap`, `session.effective_mode`, include both in `snapshot()` and `metrics()`; unknown values -> `dropped += 1`.
  3. `RemotePipeline`: replace `_privacy_mode: bool` with `_station_cap: str` (from `mode_state`, default `"full"`), `_policy_mode: str` (household/owner choice), keep `get_privacy()` returning `effective == "blackout"` for the existing `/privacy` GET and `status()['privacy_mode']` (frontend `useBackend` and `VideoPanel` read it). New `set_policy(mode, preview=None, audio=None, viewers=None) -> bool` posting the `policy` downlink and, for compatibility, the legacy `privacy` downlink (`enabled = mode == "blackout"`).
  4. `EventUploader.handleServerText`: add `"policy"` and `"mode_request"` branches calling new listener methods `onPolicyDownlink(mode: String, previewFps: Int, previewMaxEdge: Int, previewQuality: Int, audio: Boolean, videoViewers: Int, audioViewers: Int)` and `onModeRequest(mode: String, from: String)`; add `fun sendModeState(cap: String, effective: String, reason: String)`.
  5. `frontend/lib/types.ts`: `ViewMode = "blackout" | "privacy" | "limited" | "full"`; `PipelineStatus` gains `station_cap?`, `effective_mode?`.
- **Tests**: `tests/web/test_modes.py` (`min_mode` truth table incl. unknown -> error), `RemoteSession` `mode_state` accept/reject, `RemotePipeline.set_policy` posts both downlinks in order (use the queue/`_send` fake from existing remote pipeline tests); Kotlin: `EventUploaderTest` parses a `policy` message and calls the listener with the values; `ContractsTest`/type-sync tests still green.
- **DoD**: all suites green; `CLAUDE.md` protocol section lists `policy`, `mode_request`, `mode_state`.

## T3.2 Android enforcement (station is authoritative)
- **Files**: `MonitorService.kt`, `MainActivity.kt` (UI), new `capture/ViewMode.kt`, tests `ViewModeTest.kt`.
- **Steps**
  1. `enum class ViewMode { BLACKOUT, PRIVACY, LIMITED, FULL }` with `fun min(a,b)`. In `MonitorService` replace `privacyMode: AtomicBoolean` with `stationCap: AtomicReference<ViewMode>` (persisted in `btb_station_prefs` key `station_cap`, default FULL) and `policy: AtomicReference<Policy>` (mode, preview params, audio flag, viewer counts). `effectiveMode() = min(stationCap, policy.mode)`.
  2. Camera callback gating (`CameraSource` `frameCallback`, ~277-330):
     - `BLACKOUT`: return before any processing (as today) but **do not send the slate**; uplink carries only pings. The server renders the slate.
     - `PRIVACY`: run `pipe.processFrame` and `sendFrame/sendRules` as normal, skip `encodePreviewJpeg`/`sendPreview`.
     - `LIMITED` and `FULL`: encode a preview only when `policy.viewers.video > 0` and the interval since the last send is >= `1000 / policy.preview.fps` ms; use `policy.preview.max_edge` and `quality` instead of the hard-coded 320/60 in `encodePreviewJpeg` (parameterise it). In `LIMITED` the phone still sends the normal-resolution frame; the server blurs it (T3.5).
  3. Mic callback (`MicSource` `audioCallback`): `BLACKOUT` -> return (as today). Otherwise run `pipe.pushAudioPcm16` and `sendAudio` events as normal. Raw audio streaming is T3.9 and only in `FULL` with `policy.audio`.
  4. `setPrivacyMode(enabled)` (called by the UI Privacy button and the legacy downlink) maps to: `enabled=true` -> `stationCap = BLACKOUT`; `enabled=false` -> **only from the UI** (user on the device) restores the previous cap. The legacy `onPrivacyDownlink(false)` must NOT raise exposure: ignore it unless `policy`-capable servers are absent (never raise from a remote message). `onPrivacyDownlink(true)` lowers immediately.
  5. `onModeRequest(mode, from)`: if requested mode <= current cap: apply immediately. If it would raise the cap: show an on-device prompt (heads-up notification with **Allow 15 min / Deny** actions plus an in-app dialog if the activity is visible); default deny after 60 s; allowed raises last 15 minutes then revert to the previous cap (store `raisedUntil`). Setting `allow_owner_raise_without_prompt` (default false) skips the prompt only for requests tagged `from=owner`.
  6. Always call `uploader.sendModeState(cap, effective, reason)` after any change and on connect.
  7. UI (`MainActivity` controls panel): replace the single Privacy toggle with a 4-segment control (Blackout / Privacy / Limited / Full) bound to `stationCap`, with one-line descriptions. Keep button ids/labels used by the ambient overlay in sync (`updatePrivacyButton`).
- **Tests** (pure JVM): `ViewModeTest`: `min`, effective mode truth table; a small `ModeController` class extracted from `MonitorService` (so it is unit-testable without Android): remote raise is denied without approval, reduce applies immediately, approved raise expires after 15 minutes (inject a clock), legacy `privacy:false` never raises.
- **Phone check**: with the dev loop and a local backend, set modes from the station UI and watch `/status` (`effective_mode`) and logcat; in `blackout` capture `adb shell dumpsys netstats` is not needed: use the backend counters `previews`, `events` (T3.8 formalises the check).
- **DoD**: android tests green; debug APK builds; phone check recorded.
- **Pitfalls**: keep `FLAG_KEEP_SCREEN_ON`/Station Dim behaviour untouched; `rotation` handling in `encodePreviewJpeg` must stay; preview and inference currently share the `submitLatest` lane (Phase 5 will decouple them), do not restructure it here.

## T3.3 Backend policy engine, endpoints and ceilings
- **Files**: new `backend/web/policy.py`; edit `backend/main.py`, `backend/web/remote_pipeline.py`, `backend/web/runtime.py`, `backend/web/hub.py`.
- **Steps**
  1. `Policy` per household: owner-selected `household_mode` (default `privacy` for new households: safest default), computed `preview` params (T4.3/T5.2 later), `viewers` counts (T3.4). `compute_effective(viewer) = min_mode(station_cap, household_mode, viewer.mode_ceiling)`.
  2. Endpoints (use `ctx`): `GET /mode` -> `{household_mode, station_cap, effective}` for the caller; `POST /mode {mode}`: owner only; lowering applies immediately (`set_policy` + publish status); raising above `station_cap` sends `mode_request {mode, from:"owner"}` to the phone and returns `{"status":"pending"}` (the dashboard shows "Waiting for approval on the phone"); viewers get 403.
  3. Keep `POST /privacy` and `GET /privacy` as thin compatibility wrappers: `enabled:true` -> `POST /mode blackout`; `enabled:false` -> request to restore the previous household mode (never directly raises; returns pending/approved like above); `GET` returns `{"privacy_mode": effective=="blackout"}`.
  4. `GET /video`: compute the caller's effective mode **per request**: `blackout` -> serve the server-rendered slate (existing `_privacy_jpeg`) and never the real frame; `privacy` -> slate variant "Events only" (new text), `limited` -> T3.5 processed image, `full` -> raw. Re-evaluate every frame inside the generator so a mode change applies to open streams within one frame.
  5. `/ws` frame/audio/rules/emotion envelopes: in `blackout` the phone sends none; nothing extra to filter server-side. Add the caller's `effective_mode` to the initial `status` message and to every `status` publish (`Runtime.status()` gets `mode_info`).
  6. `Hub`: `subscribe(viewer_id=None, role=None)` stores metadata so T3.4 can count and kick; keep existing signature working (default None) because tests call `hub.subscribe()`.
- **Tests** (`tests/web/test_policy.py`): viewer with `mode_ceiling="limited"` cannot get raw frames from `/video` even when the station cap is full; owner `POST /mode full` above the cap returns pending and the phone fake receives `mode_request`; lowering to `privacy` makes an already-open `/video?max_frames=` stream switch to the slate; `/privacy` wrapper behaviours; viewer `POST /mode` -> 403; isolation: H1 mode change never affects H2 (add to `test_isolation.py`).
- **DoD**: green including T2.7.

## T3.4 Viewer tracking and anti-lurking (new behaviour; AGENTS.md currently overclaims)
- **Backend**: count active `/video` generators and `/ws` subscribers per household, by viewer id: increment before streaming, decrement in a `finally` (the generator is cancelled on client disconnect). Compute `viewers = {"video": n_video, "audio": n_audio}` and `labels` (viewer label or user-agent family, e.g. "Chrome on Android", max 24 chars) and push a `policy` downlink on every change (debounce 300 ms). Add an owner endpoint `POST /viewers/disconnect-all` (and the station can request the same via the phone UI below).
- **Android**: new listener handling in `onPolicyDownlink`: when `videoViewers` goes 0 -> >0 play a **two-tone chime** (`ToneGenerator` `TONE_PROP_ACK` then `TONE_PROP_BEEP2`, modelled on `playTreatChime`) and update a persistent on-screen indicator "N viewers watching" (also in the foreground notification text: update `buildNotification(statusText)` and re-post), cleared at 0. A **Disconnect viewers** button/notification action sends `{"type":"kick_viewers"}`; the backend closes viewer sockets with 4411 and rejects `/video` for 60 s. Respect Do-not-disturb: use `STREAM_NOTIFICATION`.
- **Dashboard**: 180 s inactivity watchdog in `VideoPanel.tsx`: after 180 s of continuous viewing (no pointer/keyboard interaction) stop the MJPEG (`<img>` removed) and show "Stream paused to protect household privacy. Tap to resume"; resume creates a new `streamId`. Make the timeout configurable (`web.video.max_idle_s`, default 180, delivered in `/status`) and exempt when the page is hidden (`document.visibilityState`): a hidden tab stops the stream immediately.
- **Tests**: backend: opening two `/video` streams as two viewers yields `viewers.video == 2` in the downlink queue; closing one -> 1; kick -> 4411 and 60 s cool-down (inject clock); vitest for the watchdog timer logic extracted into `frontend/lib/watchdog.ts` (pure, fake timers).
- **Phone check**: open the dashboard in the browser while the phone is in `full` mode: chime plays, indicator shows 1; close the tab: indicator clears.
- **Docs**: fix `AGENTS.md` section 4 so every invariant states what is implemented (after this task the chime/badge/watchdog are real; the rest stays "planned") and update `docs/privacy_architecture_and_threat_model.md` section 4 status table.
- **DoD**: green; AGENTS.md matches reality.

## T3.5 Limited mode image processing (server side)
- **Files**: new `backend/web/limited.py`; wire into `/video` (T3.3 step 4).
- **Steps**: `limit_jpeg(jpeg, *, width=240, blur=31, quality=50) -> bytes` using `cv2.imdecode`, `cv2.resize` (INTER_AREA), `cv2.GaussianBlur((blur|1, blur|1), 0)`, `cv2.imencode`. Cache the result for 10 s per household (`Limited(cache_s=10, clock=...)`), so N limited viewers cost one decode per 10 s. **Peek**: `POST /peek` (viewer or owner) returns a one-time 15 s window in which that viewer's effective mode is raised to `min(full, viewer.mode_ceiling)` only if the station cap is `full`; it also pulses the chime (`policy.viewers` change) and is logged in a per-household in-memory audit list (T7). If the cap is lower, `/peek` -> 409 `{"error":"station_cap"}`.
- **Tests**: `limit_jpeg` shrinks width and reduces high-frequency energy (Laplacian variance of the output < input on a synthetic noisy image), cache returns identical bytes within 10 s and refreshes after; `/peek` window expiry with an injected clock; peek denied under `privacy`/`blackout` caps.
- **DoD**: green; manual screenshot of a limited viewer vs a full viewer attached to the PR.

## T3.6 Dashboard mode UI
- **Files**: new `frontend/components/ModeSheet.tsx`, `VideoPanel.tsx`, `Dashboard.tsx`, `useBackend.ts` (replace `setPrivacy` with `setMode`/`requestMode`), `lib/modes.ts` (+ test).
- **Steps**: a bottom sheet (opened from the video controls) with four options and one-line explanations ("Full: live video and sound", "Limited: blurred snapshots every 10 s", "Privacy: barks and behaviour only", "Blackout: nothing leaves the phone"). Options above the viewer's ceiling are disabled with a lock icon and tooltip. Raising shows "Waiting for approval on the phone…" with a 60 s countdown and handles `pending -> approved/denied` via the `status` stream (`effective_mode`). The video overlay text is derived from `effective_mode` (replacing the current `privacyMode` boolean overlay "Family Privacy Active"). Keep `privacy_mode` handling for old servers.
- **Tests**: `lib/modes.test.ts` (ordering, `min`, label/description table, which options are enabled for each ceiling); no component tests unless RTL exists.
- **DoD**: vitest, lint, build green; screenshots at 390 px for each mode.

## T3.7 Live audio spike (gated by D3; do before T3.9)
- **Questions to answer with measurements on the A07**: (a) raw PCM16 mono 16 kHz in 100 ms chunks (3,200 bytes, about 256 kbps) over the existing socket: CPU, battery drain over 10 minutes, dashboard latency; (b) Opus (or AAC) via `MediaCodec`: encoder availability across API 26-36 (Opus encoder is API 29+; AAC is universal), bitrate (24-32 kbps), added latency; (c) browser playback path: WebAudio `AudioWorklet` ring buffer vs `MediaSource`; target end-to-end latency under 1 s.
- **Output**: `docs/superpowers/plans/2026-10-10-multi-household/audio-spike.md` with a table (codec, kbps, latency, battery %/10 min, notes) and a recommendation. No production code merges from the spike.
- **Constraint**: the mic is already hot for YAMNet (`MicSource` at 16 kHz, 100 ms chunks); streaming reuses the same `AudioRecord` chunks, so no second recorder.

## T3.8 End-to-end mode verification and docs
- **Capture test** (`tests/web/test_mode_wire.py`): using the simulated station, assert what the server *receives* per mode: in `privacy` the station fake sends no `0x01` previews because the policy has `video viewers = 0` or `mode=privacy` (assert the phone model obeys by implementing a tiny reference station in the test that follows the policy rules; the real enforcement is unit-tested in Kotlin `ModeController`). On the real phone: with `adb shell tcpdump` unavailable, use backend counters (`previews`, `events`, `dropped`) over 30 s in each mode and record them in `docs/BASELINE_2026-10-10.md`: expected `previews == 0` in privacy and blackout, `events == 0` in blackout, `audio_ok == false` in blackout.
- **Docs**: update `docs/privacy_architecture_and_threat_model.md` (modes table, station sovereignty, approval flow), `CLAUDE.md` protocol, `AGENTS.md` section 4, `docs/USER_ONBOARDING_GUIDE.md` (what each mode means for family members).
- **DoD**: all suites incl. T2.7 green; recorded counter table.

## T3.9 Live audio implementation (only if D3 = yes)
- **Phone**: after the spike's chosen codec, send binary `0x02 + payload` (document the framing in `ingest_events.py`; add `KIND_AUDIO = 0x02` handling in `RemoteSession.handle`) only when `policy.audio && viewers.audio > 0 && effective == full`; show a distinct "listening" indicator and chime when `viewers.audio` goes 0 -> >0.
- **Backend**: `RemoteSession.handle` forwards `0x02` chunks to `pipeline.ingest_raw_audio` (existing `ingest_audio` stub records the timestamp only) -> household `AudioHub`; new `GET /audio` WebSocket (binary) per viewer with the same `ctx` checks, effective mode must be `full`, and a bounded queue per client (drop oldest).
- **Dashboard**: `Listen` toggle, `lib/audioPlayer.ts` with a 300 ms jitter buffer; default muted; user-gesture requirement for autoplay handled.
- **Tests**: size/framing parser tests, audio never delivered to `limited/privacy/blackout` viewers, isolation across households (extend T2.7), queue drop behaviour.
