# Phase 0 (environment) and Phase 1 (quick fixes)

Read `README.md` in this folder first (global rules, dev loop, hand-off template).

Facts all tasks here rely on (verified 2026-10-10, commit a766e48):
- Stop bug evidence: tapping **Stop Monitoring** on the phone produced backend log `phone disconnected: DebugPhone (code 1005)`, logcat `EventUploader: WebSocket failed (...): Socket closed`. Cause: `EventUploader.stop()` (`android/app/src/main/java/com/btb/ondevice/net/EventUploader.kt:110-126`) does `webSocket?.close(CLOSE_NORMAL, "Client stopped")` and then immediately `webSocket?.cancel()`, so the close frame is never sent.
- Backend: `Runtime.phone_close` (`backend/web/runtime.py`, "phone_close") only treats close code `1000` as a deliberate stop (phone forgotten, `_phone_prev=None`). Anything else keeps `_phone_prev` with `connected: False`, which `frontend/lib/phone.ts phoneNotice()` maps to `"dropped"` -> "Reconnecting to camera".
- Separately, `RemotePipeline._state()` (`backend/web/remote_pipeline.py`) returns `"stalled"` whenever the newest frame/preview is older than `stale_s` (2 s) **or no frame was ever seen** (`since = now - self._started_at`), and `Dashboard.tsx:47-48` turns any `stalled` into `paused`, so `VideoPanel.tsx:221-230` shows "Reconnecting…" and `SystemNotice.tsx:75-83` shows "Reconnecting to camera". Consequence: a **fresh server with no phone ever connected also shows "Reconnecting…"** (verified: `/status` on a just-started backend returned `state: "stalled"`).
- `Runtime.status()['fps']` comes from `_frame_times` (last 64 FrameEvent ts) and is not aged out, so the dashboard keeps showing e.g. "10 fps" after a stop.
- Existing tests that must keep passing: `tests/web/test_ingest_events.py::test_status_is_stalled_after_camera_silence_and_mirrors_phone_metrics` (attaches a session, so it is unaffected by a "never attached -> idle" rule) and `::test_silence_marks_the_pipeline_stalled_and_the_label_unknown`.

---

## Phase 0: environment

### T0.1 Install a JDK and prove the Android toolchain
- **Why**: no `java` on this machine; `/tmp/opencode/jdk` from AGENTS.md is gone; the Android unit tests and APK builds have never been run in this session.
- **Steps**
  1. Install JDK 17 (`sudo apt install openjdk-17-jdk`, or download Temurin 17 into `~/jdk17`). Do not run `sudo` without the user's approval; if sudo is unavailable use the tarball in `~/jdk17`.
  2. `export JAVA_HOME=<path>; export PATH=$JAVA_HOME/bin:$PATH; export ANDROID_HOME=$HOME/Android/Sdk`. Confirm `java -version` is 17 and `ls $ANDROID_HOME/platforms` contains `android-35`.
  3. `cd android && ./gradlew testDebugUnitTest` (first run downloads Gradle and dependencies; allow 10+ minutes; run in background with a log file).
  4. `./gradlew assembleDebug`, then `$ANDROID_HOME/build-tools/35.0.0/zipalign -c -P 16 4 app/build/outputs/apk/debug/app-debug.apk` (check the build-tools folder actually has 35.0.0; adjust the path if not).
  5. Edit AGENTS.md section 2 so the JDK path in the commands is the real one.
- **DoD**: test task passes (write the real test count in the hand-off; AGENTS.md claims 136, about 30 test classes exist), debug APK exists and passes zipalign.
- **Pitfalls**: `gradle/libs.versions.toml` pins okhttp 5.3.2 on purpose (comment explains compileSdk 35); do not upgrade. If Gradle needs offline mode or a proxy, report instead of editing build files.

### T0.2 Record baselines
- Create `docs/BASELINE_2026-10-10.md` with: pytest count, vitest count, lint result, android test count, debug and release APK sizes, and the phone facts (SM-E075F, Android 16/API 36, arm64-v8a). Leave a table with empty rows "preview bytes/s", "preview fps", "latency" for T5.1 to fill.
- **DoD**: file committed; numbers copied from real command output.

### T0.3 Put the current build on the phone
- **Why**: installed app is 0.1.0-scaffold updated 2026-10-04; source and the release APK are from 2026-10-08.
- **Steps**: `adb devices -l` shows the device as `device` (not `unauthorized`); `adb install -r android/app/build/outputs/apk/debug/app-debug.apk` (debug build keeps debug signing; if the installed app was signed with the release key, install fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`: then ask the user before `adb uninstall com.btb.ondevice`, because that deletes the app's prefs and spool). Check `adb shell dumpsys package com.btb.ondevice | grep -E "versionName|lastUpdateTime"`. Grant nothing manually; camera/mic permissions persist across `install -r`.
- **DoD**: lastUpdateTime is today; re-run the stop reproduction from the README dev loop on the fresh build and record that the server still logs `code 1005` (this is the "before" state for T1.1).

### T0.4 Scripted dev loop and simulated phone
- Create `scripts/dev_phone_loop.sh` implementing the README dev loop with subcommands `start-backend | start-phone | stop-phone | status | shot <width> <out.png> | clean`. `stop-phone` must locate the "Stop Monitoring" button via `uiautomator dump` (regex on `text="Stop Monitoring"` then centre of `bounds`), never hard-coded coordinates.
- Create `scripts/sim_phone.py` (a `websockets` client): args `--url`, `--device`, `--seconds`, `--stop {bye,close1000,drop}`; sends the hello, a preview JPEG (`b"\x01"+small valid jpeg`) and a FrameEvent envelope per 0.2 s, then ends the session in the chosen way. This replaces the throwaway `/tmp/sim_stop.py` used on 2026-10-10 and is what T1.2, T2.7 and T4.4 reuse.
- **DoD**: `scripts/dev_phone_loop.sh start-backend` then `python scripts/sim_phone.py --stop drop` shows `phone disconnected (code ...)` in the backend log; `shot 390 /tmp/a.png` produces a PNG.

---

## Phase 1: quick fixes

### T1.0 Remove the public PIN (security, do first)
- **Context**: `render.yaml` has `BTB_DASHBOARD_PIN` with value `"1234"`; GitHub repo `07AMIT10/BehindTheBarks` is PUBLIC (checked with `gh repo view`). `settings.py:89-91`: setting that env var enables auth with that PIN.
- **Steps**
  1. In `render.yaml` replace the literal with a secret placeholder. Commit `88ae753` ("remove sync field from render.yaml envVar") shows Render rejected a `sync` key before, so confirm the current Blueprint schema (Render docs: `sync: false` marks a secret to be set in the dashboard) before choosing; if unsure, delete the env var from the file and document "set BTB_DASHBOARD_PIN in the Render dashboard" in `docs/USER_ONBOARDING_GUIDE.md` and README.
  2. `git log -p -S1234 -- render.yaml` to confirm the PIN is in history; note in the hand-off that history still contains it (do not rewrite history).
  3. Add to the hand-off: "ask the user to set a new PIN in Render now".
- **DoD**: `grep -rn "1234" render.yaml docs README.md` shows no PIN. No behaviour change locally (`tests/web/test_auth_privacy.py` still uses "1234" as a test value, which is fine).

### T1.1 Android: send a real stop
- **Files**: `android/app/src/main/java/com/btb/ondevice/net/EventUploader.kt`, `android/app/src/test/java/com/btb/ondevice/net/EventUploaderTest.kt`.
- **Steps**
  1. Add `const val STOP_GRACE_MS = 1500L` to the companion object.
  2. Rewrite `stop()`:
     ```kotlin
     fun stop() {
         if (!isRunning.compareAndSet(true, false)) return
         val ws: WebSocket?
         synchronized(lock) {
             state = ConnectionState.TERMINATED
             reconnectJob?.cancel()
             pingJob?.cancel()
             ws = webSocket
             webSocket = null
         }
         if (ws != null) {
             try { ws.send("""{"type":"bye"}""") } catch (_: Exception) {}
             try { ws.close(CLOSE_NORMAL, "Client stopped") } catch (_: Exception) {}
             // never cancel immediately: cancel() discards the queued close frame (observed as code 1005)
             scope.launch {
                 delay(STOP_GRACE_MS)
                 try { ws.cancel() } catch (_: Exception) {}
             }
         }
         listener?.onStateChanged(ConnectionState.TERMINATED)
     }
     ```
  3. In `handleFailure` and `handleClose`, return early when `state == TERMINATED && !isRunning.get()` so a late `onFailure`/`onClosed` after a deliberate stop does not overwrite TERMINATED with DISCONNECTED and does not notify the listener again. (Today `handleFailure` sets `state = DISCONNECTED` unconditionally; `setState()` is the only guarded path.)
  4. Replace the string-built hello in `onOpen` with `buildJsonObject { put("type","hello"); put("proto",1); put("device",deviceName); put("phone_time",phoneTime); put("models", buildJsonObject {}); put("profile","mobile"); if (!token.isNullOrBlank()) put("token", token) }.toString()` (kotlinx.serialization.json is already imported in this file). Field names and order are not significant to the server, but keep `"proto":1` and `"device":"..."` byte-compatible for the existing `handshake...` test (it uses `contains`).
- **Tests** (add to `EventUploaderTest.kt`, same MockWebServer pattern as `terminal close code terminates uploader without reconnect`):
  - `stop sends bye then a normal close frame`: server-side `WebSocketListener` completes a `CompletableDeferred<String>` for text containing `"type":"bye"` and another for `onClosing(code=1000)`; call `uploader.stop()` after CONNECTED; assert both complete within 3 s and `uploader.connectionState == TERMINATED`.
  - `device name with quotes still produces valid hello json`: `deviceName = "Dog \"Cam\"\\1"`; parse the received hello with `Json.parseToJsonElement` and assert `device` equals the raw name.
  - `late failure after stop does not resurrect the uploader`: stop, then assert state stays TERMINATED after 2 s and `server.requestCount == 1` (no reconnect).
- **Phone check** (after T0.3): run the README loop; tap Stop. Backend log must show `phone stopped: DebugPhone (code 1000)` (or `bye`), not 1005; logcat must not show `Socket closed`.
- **DoD**: android tests green, phone check done, `zipalign` check if an APK is built.
- **Pitfalls**: `ws.send` after `close` is rejected by OkHttp (returns false), so `bye` must be sent first. `MonitorService.stopMonitoring()` may kill the process soon after `uploader?.stop()`; if the process dies before the flush the server falls back to the 1005 path, which T1.2 does not treat as a stop (that is correct: it is indistinguishable from a crash).

### T1.2 Backend: model "stopped" and "idle" explicitly
- **Files**: `backend/web/ingest_events.py`, `backend/web/runtime.py`, `backend/web/remote_pipeline.py`, `backend/main.py` (only if needed), tests listed below.
- **Steps**
  1. `RemoteSession.__init__`: add `self.stopped = False`. In `handle_text` after the `ping` branch and **before** the `data = msg.get("data")` line add: `if kind == "bye": self.stopped = True; return "bye"`. Update the module docstring protocol list (`text {"type":"bye"} ... deliberate stop`).
  2. `Runtime.phone_close`: `stopped = code == 1000 or bool(getattr(session, "stopped", False))`. In the `stopped` branch call `getattr(self.pipeline, "mark_stopped", lambda: None)()` **before** `self.publish_phone()` so the broadcast carries the new state. Keep the early return for `self.phone is not session` (replaced sessions must not mark the pipeline stopped).
  3. `RemotePipeline`: add `self._idle = False` and `self._ever_attached = False`. `attach()` sets `_ever_attached = True; _idle = False`. Add
     ```python
     def mark_stopped(self) -> None:
         """The phone said bye / closed with 1000: forget its last frame so the dashboard shows
         'idle' instead of 'stalled'."""
         self._jpeg, self._preview_ts, self._idle = None, None, True
     ```
     `_state()` order: `if self._stopped: "stopped"`; `if self._idle or not self._ever_attached: "idle"`; then the existing stale logic. `latest_frame_jpeg()` already returns `self._jpeg` (None after mark_stopped) except in privacy mode; make privacy mode not return the slate when idle (`if self._idle: return None` first).
  4. `Runtime.status()`: compute `fps` as 0.0 when `not ft or self.clock() - ft[-1] > 3.0` (use `self._stale_s`-independent constant `FPS_FRESH_S = 3.0`).
  5. `/video` in `main.py`: keep the loop but stop streaming placeholder frames when idle (already skipped because `jpeg` is None). No change needed unless a test shows the generator never ends; leave as is.
  6. `CLAUDE.md`: add `bye` to the on-device protocol description and `idle` to the status states.
- **Tests**
  - Unit (`tests/web/test_ingest_events.py`): `handle_text('{"type":"bye"}') == "bye"` and `session.stopped is True`; `dropped` unchanged.
  - Unit: `RemotePipeline` new rules: fresh instance -> `status()["state"] == "idle"`; after `attach` -> `"running"`; after `mark_stopped` -> `"idle"` and `latest_frame_jpeg() is None`; attach again -> `"running"`; existing stalled test still passes.
  - Runtime (`tests/web/test_runtime.py` style): `phone_close(session, code=1005)` after `session.stopped=True` -> `rt.phone is None`, `phone_status() is None`, pipeline idle; `phone_close(session, code=1005)` without `stopped` -> `phone_status()["connected"] is False` (drop path unchanged); replaced session (`rt.phone is not session`) -> pipeline untouched.
  - WebSocket integration (new `tests/web/test_remote_stop.py`, use `remote_client(tmp_path, ...)` helpers from `test_ingest_events.py`): connect, send a frame envelope and preview, send `{"type":"bye"}`, close; poll `/status` until `phone is None` and `pipeline_status.state == "idle"`; assert it never reports `"stalled"` in 3 s (`stale_s` is small in tests; poll for 2x `stale_s`). Second test: same flow ending with an abrupt close without bye -> `phone.connected is False`.
  - Status fps: after frames then 4 s of fake clock, `status()["fps"] == 0.0`.
- **DoD**: pytest green (259 + new); with the real phone and the T1.1 build, Stop leaves `/status` as `phone: null`, `state: idle`, `fps: 0`.
- **Pitfalls**: `main.py` calls `rt.pipeline.detach(session)` in `finally` **before** `rt.phone_close(...)`; do not reorder. `MockPipeline` and `Pipeline` have no `mark_stopped` (use `getattr`). Do not change `CLOSE_*` constants.

### T1.3 Dashboard: "Monitoring stopped", never "Reconnecting", after a stop
- **Files**: `frontend/lib/types.ts`, `frontend/lib/phone.ts` (+ `phone.test.ts`), `frontend/components/Dashboard.tsx` (lines 47-48), `VideoPanel.tsx` (~170-230), `SystemNotice.tsx` (~63-83), `StatusBar.tsx`, `EmotionCard.tsx` (paused text), `frontend/lib/store.test.ts`.
- **Context**: `store.ts` merges partial `status` envelopes: `withStatus(base, {...base.status, ...d})`; `Runtime.publish_phone()` sends `{phone, pipeline_status}`, so `phone: null` and `state: "idle"` arrive together on stop.
- **Steps**
  1. `types.ts`: `PipelineStatus.state: "running" | "stalled" | "stopped" | "idle" | string` (already `| string`, but add the literal for narrowing).
  2. `phone.ts`: add
     ```ts
     export type StationState = "idle" | "live" | "stalled" | "dropped" | "blocked";
     export function stationState(s: Status | null): StationState {
       const p = s?.phone; const ps = s?.pipeline_status?.state;
       if (p?.connected && p.camera === false) return "blocked";
       if (p && !p.connected) return "dropped";
       if (ps === "idle") return "idle";
       if (ps === "stalled") return "stalled";
       return "live";
     }
     ```
     and make `deviceLabel` return `"No camera streaming"` when `ps === "idle"` and no phone (keep the other branches).
  3. `Dashboard.tsx`: `const st = stationState(state.status); const idle = st === "idle"; const stalled = st === "stalled"; const paused = (!state.connected && state.everConnected) || stalled || st === "dropped";` (keep `notice` for the blocked card). Pass `idle` to `VideoPanel`, `EmotionCard`.
  4. `VideoPanel.tsx`: new prop `idle?: boolean`. When idle: do not render the `<img>` (no `/video` request), show a neutral card (no spinner): title "Monitoring stopped", body "Start monitoring in the WagWatch app, or pair a phone from the top bar." Keep the existing "Reconnecting…" overlay only for `paused`. Include `idle` in `streamId` so a fresh MJPEG stream opens when monitoring resumes (current code uses `paused ? "p" : ""`).
  5. `SystemNotice.tsx`: show "Reconnecting to camera" only for `dropped` or `stalled`; render nothing for `idle`. For `dropped` older than 60 s change copy to "Camera offline since hh:mm" (needs `phone.last_event_age_s`; if absent keep the current text).
  6. `StatusBar.tsx`: when idle show "Station offline" with the muted dot instead of "Connected" for the camera chip, and hide the `N fps` chip when `fps === 0`.
  7. `EmotionCard`: when `idle`, replace "PAUSED · LAST READING" with "NO LIVE READING" (read the component first; keep its props backward compatible).
- **Tests**
  - `phone.test.ts`: table for `stationState`: `phone:null,state:"idle"` -> idle; `phone:null,state:"running"` -> live (mock/browser sources); `phone:null,state:"stalled"` -> stalled; phone connected+running -> live; phone connected, `camera:false` -> blocked; phone `connected:false` -> dropped; `deviceLabel` idle string.
  - `store.test.ts`: reducer applies a partial status `{phone:null, pipeline_status:{state:"idle",...}}` over a live full status and `stationState` returns idle.
  - Add a small component test only if the repo already has React Testing Library (it does not appear to: check `package.json`); otherwise verify visually.
- **Acceptance**: with the dev loop on the real phone: stop -> screenshot at 390x844 shows "Monitoring stopped" card and no "Reconnecting" text anywhere (`grep` the page text via `--dump-dom`: `google-chrome --headless=new --dump-dom http://localhost:8000/ | grep -c Reconnecting` must be 0 after stop; use `--virtual-time-budget=8000`). Kill the phone app with `adb shell am force-stop` instead -> screenshot shows "Reconnecting to camera" (true drop). Fresh backend with no phone -> "Monitoring stopped"/"No camera" card, not "Reconnecting".
- **DoD**: vitest + lint + `npm --prefix frontend run build` pass; screenshots saved under `docs/screenshots/2026-10-10/` (before and after).

### T1.4 Neutral dog profile (remove Bruno/Kitchen)
- **Occurrences to change** (grep `-rniE "bruno|kitchen"`): `config.yaml:194-196` (`profile.dog_name`, `location`), `backend/web/settings.py:39`, `frontend/components/Dashboard.tsx` (fallback profile object), `frontend/components/Header.tsx` (renders `WagWatch · {location}` and `Feeding area · {location}`), tests `tests/web/test_settings.py:18`, `tests/web/test_runtime.py:153`, `tests/web/test_notify.py:22-32` (a fixture constant; may stay but rename for the grep), `frontend/lib/store.test.ts`, `toasts.test.ts`, `demo.test.ts`, `docs/DEMO_RUNBOOK.md` (lines 41, 51). `android/.../QrDecoderTest.kt` "Kitchen-Cam" is just a sample device name and may stay.
- **Steps**
  1. New defaults: `dog_name: "Your dog"`, `location: ""`, `zone_label: "feeding area"` in `settings.py` DEFAULTS and `config.yaml`.
  2. `Header.tsx`: render the location segment only when non-empty (mobile: `"WagWatch"`; desktop: `"Feeding area"`).
  3. `Dashboard.tsx` fallback profile: `{dog_name: "your dog", location: "", zone_label: "feeding area"}`.
  4. `backend/notify/base.py caption_for` already joins only non-empty `location`/`zone_label` (`" · ".join(x for x in ...)`); add a test with `profile={"dog_name":"Rex","location":"","zone_label":"feeding area"}` asserting the caption has no leading/trailing `" · "`.
  5. Update the tests above to the new defaults/neutral fixtures ("Buddy", "Living room").
  6. Profile editing UI is Phase 2 (T2.3); until then the profile comes from config/env only. Add env override `BTB_DOG_NAME` and `BTB_LOCATION` in `settings.py load_config` (follow the existing `env.get(...)` pattern) so a deployer can name the dog without editing YAML; unit test in `tests/web/test_settings.py`.
- **DoD**: `grep -rniE "bruno|kitchen" backend frontend/components frontend/lib config.yaml tests docs/DEMO_RUNBOOK.md` returns nothing except intentional sample data; all suites green.

### T1.5 Treat downlink parsing
- **Context**: server sends `{"type":"treat","data":{"ts":<phone-clock secs>,"offset_s":<float>}}` (`remote_pipeline.py mark_treat`); the app's `handleServerText` reads `root["ts"]` (absent) and falls back to `clock()`.
- **Steps**: in the `"treat"` branch read `root["data"]?.jsonObject?.get("ts")?.jsonPrimitive?.double ?: root["ts"]?.jsonPrimitive?.double ?: clock()`. Keep the fallback to root `ts` for any legacy server.
- **Tests**: extend `downlink treat triggers listener` in `EventUploaderTest.kt` (it currently sends one shape; read it first) with a second case using the real server shape `{"type":"treat","data":{"ts":123.5,"offset_s":2.0}}` and assert `onTreatDownlink(123.5)`.
- **DoD**: android tests green. Also confirm `pipe.pushTreat(ts)` in `MonitorService` expects phone-clock seconds (it does per the server comment).

### T1.6 Phone-friendly layout
- **Baseline screenshot (2026-10-10, 390x844)**: header, then a wrapped StatusBar (connection, device, `N fps`, "Rules only", Live/Demo toggle), a notice banner, a 260 px video card whose controls (photo, Light, flip, Privacy, Box/Skel/Face) are packed in the bottom bar of the video, the emotion card, a collapsed Signals row, and a fixed bottom Drop Treat bar.
- **Files**: `frontend/components/Dashboard.tsx`, `Header.tsx`, `StatusBar.tsx`, `VideoPanel.tsx`, `EmotionCard.tsx`, `SignalsPanel.tsx`, `Timeline.tsx`, `TreatButton.tsx`, `frontend/app/globals.css` (find with `ls frontend/app`).
- **Steps**
  1. Add `scripts/shot.mjs`: `cd /tmp && npm i playwright-core`, then a Node script using `chromium.launch({executablePath:"/home/amit/.local/bin/google-chrome"})` (playwright-core has no browsers; the system Chrome path above works headless) that opens a URL at a given width/height, waits for network idle, saves a PNG, and prints `document.documentElement.scrollWidth <= window.innerWidth`. (Playwright libraries are not installed in the repo or venv; do not add them to `frontend/package.json`.)
  2. StatusBar on `<sm`: collapse to one line: connection dot + device label + (battery slot, Phase 6). Move the Live/Demo toggle into an overflow/settings sheet (it is a developer feature); hide the LLM chip and `N fps` unless debug mode (T6.3).
  3. VideoPanel on `<sm`: video fills the width at 16:9 (`aspect-video`) instead of fixed `min-h-[260px]`; overlay buttons keep >=44 px hit areas; secondary actions (Light, Flip, Rotate, Privacy, layers) move into a bottom sheet toggled by a single "controls" button.
  4. Order on mobile: header, video, emotion card, treat bar (fixed), Signals (collapsed), Timeline (collapsed behind a "Timeline" disclosure).
  5. Touch targets: all buttons >=44x44 CSS px (`min-h-11 min-w-11`); body text >=14 px; respect `env(safe-area-inset-*)` (already used on the treat bar).
  6. Capture before/after screenshots at 360x740, 390x844, 430x932, plus a 1280x800 desktop check that the desktop layout is unchanged.
- **Acceptance**: `scrollWidth <= innerWidth` at all three phone widths; screenshots committed; vitest, lint, build pass; no change to desktop grid areas (`lg:` classes untouched except where listed).
- **Out of scope**: new features, mode switcher (T3.5), camera grid (T4.2).

### T1.7 First-run empty state
- **Context**: with no phone ever attached the dashboard (after T1.2) shows the idle card; make it useful for a new tester.
- **Steps**: in `VideoPanel` idle card add a primary button "Pair a phone" that opens `PairingModal` (`Dashboard.tsx` already has `isPairingOpen`), plus one line of instructions ("Install the WagWatch app, open it, scan this QR"). Pass an `onPair` prop.
- **Tests/DoD**: vitest unaffected; manual screenshot of the empty state at 390 px; button opens the modal.

---

## T1.8 User-editable pet profile (added after user request 2026-10-10)

Spec: `docs/superpowers/specs/2026-10-10-user-editable-pet-profile-design.md` (approved). One pet per
station, edited in the dashboard, persisted as `<log_dir>/profile.json`.

- **Backend**: new `backend/web/profile_store.py` (atomic save, validating load, defaults fallback,
  corrupt-file tolerance). Lifespan creates it and merges the saved file into `cfg["web"]["profile"]`
  so `Runtime.status()` and captions pick it up. Routes `GET /profile` + `PUT /profile` following the
  `/torch` pattern (`verify_request_auth` + rate limit); on save, update the cfg dict and
  `rt.hub.publish("status", {"profile": {...}})` for live updates.
- **Frontend**: `ProfileModal.tsx` (PairingModal conventions) with Pet name / Location / Zone label,
  opened by a pencil button in the header; `useBackend` gains `saveProfile(patch)`.
- **Tests**: new `tests/web/test_profile.py` (store: missing/corrupt/roundtrip/validation; routes:
  GET/PUT, broadcast to a `/ws` subscriber, 400 on invalid, 401 with auth on); one vitest case for
  the `{profile}` partial-status merge; visual screenshots at 390 px and 1280 px.
- **DoD**: pytest green, vitest green, lint+build green, screenshots saved, onboarding guide notes
  the Render ephemeral-disk fallback.
