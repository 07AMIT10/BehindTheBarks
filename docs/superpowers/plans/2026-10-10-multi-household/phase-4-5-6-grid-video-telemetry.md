# Phases 4, 5, 6: multi-station grid, video quality, battery and debug

Read `README.md` first. Phase 4 needs Phase 2 (household context, station ids) and T3.3 (policy). Phase 5 needs T4.3 (per-station stream policy). Phase 6 needs T4.1 (station channels) for per-station telemetry.

## Verified starting point (2026-10-10)

- One phone per backend: `Runtime.phone` slot, one `FusionState`, one `Hub`; a second phone gets close code `4409` (confirmed by test with the real phone connected).
- `Runtime` already encapsulates everything per phone: `FusionState`, `TriggerPolicy`, feature/audio deques, LLM, notifier, tick loop (4 Hz) and phone-status loop. It publishes through `self.hub.publish(type, data, meta)`.
- `Hub` keeps one `deque(maxlen=history)` (2000) of envelopes and `seq` counter; frames are throttled to `web.ws.frame_fps`; `/events` returns the history plus `Runtime.timeline()`.
- Dashboard store (`frontend/lib/store.ts`, `DashboardState`) is single-stream: `current`, `spans`, `history`, `audio`, `treats`, `frame`, `status`, `lastSeq`, one `clockOffset`.
- Camera path: `CameraSource.processImage` allocates a **fresh** `ByteArray(width*height*4)` for every frame (640x480 RGBA = 1.2 MB, 15 fps target), already rotated upright (`rotation=0` to the callback). `MonitorService` hands the frame to `InferenceExecutor.submitLatest {}`, which **drops the whole frame (including its preview) when the previous inference is still running**; the preview JPEG encode (`encodePreviewJpeg`, 320 px, q60) and send happen at the end of that same task, throttled to 500 ms. So preview rate <= inference rate and any preview cost delays the next inference.
- Android thermal and battery code exists but is not uplinked: `AndroidThermalProvider.getBatteryPercent()/getThermalStatus()/getThermalHeadroom()` (`ThermalGovernor.kt`), `bench/Metrics.kt` has `battery_pct`, `thermal_status`.
- Phone used for testing: Galaxy A07 (SM-E075F), `dumpsys battery` shows level, temperature (tenths of a degree C), `USB powered`; it was 93% / 31.9 C while charging.

---

# Phase 4: multi-station dashboard

## Design

- **One `Runtime` + one `RemotePipeline` per station**, reusing existing classes unchanged. `HouseholdRuntime` holds `stations: dict[station_id, StationChannel]`, `StationChannel = (runtime, pipeline, hub)`. Cap: `web.max_stations_per_household` (default 4).
- **One `Hub` per station** (so a busy station cannot evict another's history) plus a household fan-in: `Hub.add_tap(fn)` (new, called inside `publish` with the finished envelope); `HouseholdHub` registers a tap on each station hub that re-emits `{**env, "station": station_id}` to household subscribers. `/ws` subscribes to the household fan-in; `seq` stays per station, so the client tracks `lastSeq` per station.
- **Backwards compatibility**: an envelope without `station` means the default (first/only) station; routes without `station_id` act on the default. Single-station users and existing tests see no change.

## T4.1 Backend station channels
- **Files**: `backend/web/registry.py`, `backend/web/hub.py`, `backend/main.py`, `backend/web/runtime.py` (read-only: status shape), tests `tests/web/test_stations.py`, extend `test_isolation.py`.
- **Steps**
  1. `HouseholdRuntime.channel(station_id)` creates `StationChannel` lazily on first connect of that station (a `Runtime` with its own `RemotePipeline(cfg)`, `Hub`, `DashboardOnlyNotifier` unless the household enabled external notifications; `await rt.start()`).
  2. `/ingest-events` (households mode): after auth, `channel = household.channel(station.id)`; use `channel.runtime.phone_open(...)`, `bind_pipeline(channel.pipeline, ...)`. `CLOSE_BUSY` now only applies when the **same station** is already live (a second phone with the same key = replace per existing stale rules). Different stations in one household connect concurrently.
  3. Hub: `add_tap(fn)`/`remove_tap(fn)`; `HouseholdHub.subscribe()` returns a queue of tagged envelopes; `GET /events?station=<id>` and `GET /status` accept the optional `station` query parameter (validated: must belong to `ctx.household_id`, otherwise 404). `GET /status` additionally returns `stations: [{id, name, dog_name, phone, pipeline_status, effective_mode, battery}]` and keeps the legacy single-station fields filled from the default station.
  4. Station-scoped actions take `station` (query or body): `/treat`, `/torch`, `/camera/flip`, `/mode`, `/video`, `/peek`.
  5. Idle eviction (T2.2) must consider channels: evict a station channel 10 minutes after its phone closed and no viewer subscribed.
- **Tests**: two stations in one household stream concurrently and each station's events carry the right `station` tag over `/ws`; `/status.stations` lists both; `/video?station=A` never returns B's JPEG; `/treat?station=B` reaches only B's pipeline; a station id from another household returns 404; per-station `seq` independent; extend `test_isolation.py` with the 2 households x 2 stations matrix; cap of 4 stations returns close 4409 for the 5th.
- **DoD**: all suites incl. T2.7 green.
- **Pitfalls**: `Runtime.step` runs at 4 Hz per station and `_phone_loop` every `status_every_s`; with 4 stations that is 4 tick tasks per household, acceptable but watch CPU in the Render container (measure, see T6.3).

## T4.2 Frontend station-aware store and camera grid
- **Files**: `frontend/lib/types.ts`, `store.ts` (+ `store.test.ts`), `useBackend.ts`, new `components/StationGrid.tsx`, `StationTile.tsx`, edits to `Dashboard.tsx`, `VideoPanel.tsx`.
- **Steps**
  1. State shape: `DashboardState.stations: Record<string, StationView>` where `StationView` contains the existing per-stream fields (`current`, `currentSince`, `frame`, `lastDogTs`, `history`, `audio`, `spans`, `treats`, `notifications`, `lastSeq`, `status`), and `focusedStation: string`. Keep top-level selectors that read the focused station so existing components keep their props. Reducer: route each envelope by `env.station ?? "default"`; `bootstrap` per station; `clockOffset` stays global.
  2. `useBackend`: bootstrap `GET /status` (gets `stations[]`), then `/events?station=` per station; WebSocket unchanged (single socket, tagged envelopes). `lastSeq` per station for resync.
  3. `StationGrid`: layouts for 1 tile (full width), 2 (stacked on phone, side-by-side on desktop), 3-4 (2x2). Each `StationTile` shows: station/dog name, a low-rate snapshot (`/video?station=<id>&k=...` with the grid-rate policy from T4.3, or a still refreshed every 2 s via `<img>` + cache-busting), emotion chip (from that station's `current`), battery pill (T6.2), mode badge, and a status dot (idle/live/stalled/dropped via `stationState`, reusing T1.3 logic per station). Tapping a tile focuses it: the focused tile expands into the existing full `VideoPanel` + `EmotionCard` + signals + timeline; a "Back to grid" control returns. Phone layout: single column of tiles with the focused one on top; swipe between stations (CSS scroll-snap, no extra dependency).
  4. Notifications/toasts name the station ("Rex in Kitchen seems anxious").
- **Tests**: `store.test.ts`: envelopes with `station` land in the right `StationView`; untagged envelopes go to `default`; `bootstrap` per station; focus change selects the right selectors; `stationState` per station.
- **Acceptance**: screenshots at 390x844 and 1280x800 with 1, 2 and 4 simulated stations (use `scripts/sim_phone.py --station-key ...` from several processes against a local households-mode backend); no horizontal scroll at 360 px.
- **DoD**: vitest, lint, build green.

## T4.3 Stream policy by demand (bandwidth control)
- **Files**: `backend/web/policy.py`, `backend/main.py`, `RemotePipeline.set_policy`, `frontend/lib/useBackend.ts`, `StationGrid.tsx`; Android `MonitorService` already applies `policy.preview` after T3.2.
- **Steps**: viewers tell the server what they watch: `POST /focus {station, view: "grid"|"focus"|"none"}` (viewer id from the cookie; expires after 30 s without a refresh; the dashboard re-posts every 15 s and on visibility change). Server demand per station = max over viewers: `focus` -> `preview {fps: 3, max_edge: 640, quality: 70}` (values refined in T5.2), `grid` -> `{fps: 0.5, max_edge: 240, quality: 50}`, none -> `viewers.video = 0` (phone stops encoding). Policy downlink is debounced and sent only on change.
- **Tests**: viewer A focusing station 1 and viewer B grid-viewing station 1 -> policy is the focus policy; both leave -> `viewers.video == 0`; demand expires after 30 s (inject clock); per-station isolation.
- **DoD**: green; with the real phone, backend `previews` counter stops incrementing when the dashboard tab is closed (record the observation).

## T4.4 Multi-station tests and simulated fleet
- Extend `scripts/sim_phone.py` with `--keyfile` (persist a P-256 key, pair once with `--pair-code`), so 4 simulated stations can run for soak tests. Add `tests/web/test_stations_soak.py` (marked slow) that runs 4 stations x 10 s and asserts no cross-tagging and bounded queue sizes (`downlinks_dropped == 0`).

---

# Phase 5: video quality (measure first)

## T5.1 Baseline measurement
- **Script**: `scripts/measure_stream.py --url http://localhost:8000 --seconds 60` connecting as a viewer to `/video`, parsing the multipart stream: frames, fps, total bytes, bytes/s, mean JPEG size, JPEG dimensions (via `cv2.imdecode`), longest inter-frame gap. Run it against the real phone (dev loop) in a still scene and a moving scene.
- **Latency**: manual method documented in the script's `--help`: show a millisecond stopwatch on a laptop, point the phone at it, put the dashboard next to the stopwatch and take a screenshot; latency = displayed difference. Take 5 samples.
- **Record** in `docs/BASELINE_2026-10-10.md`: expected (from the code) 320 px long edge, JPEG q60, about 1.4 fps observed (V7), around 6-15 KB per frame. Include phone temperature before/after 10 minutes via `adb shell dumpsys battery | grep temperature`.

## T5.2 Decouple preview from inference and make it configurable
- **Why**: preview encode runs inside the inference task and shares its drop policy (see verified facts), which caps fps and adds latency to inference.
- **Files**: `MonitorService.kt`, new `capture/PreviewEncoder.kt`, `EventUploader.kt`, tests `PreviewEncoderTest.kt`.
- **Steps**
  1. `PreviewEncoder(executor: single-thread, clock)`: `offer(rgba, w, h)` stores the newest frame in an `AtomicReference` if `now - lastEncodeStart >= 1000/fps` (frames are already fresh arrays, so no copy is needed), and a worker encodes: downscale to `max_edge` (`Bitmap.createScaledBitmap`, filter true) then `compress(JPEG, quality)`; parameters come from the current `policy.preview` (T3.2) and are updated live. Reuse `ByteArrayOutputStream` and a bitmap pool to cut GC (the camera already allocates 1.2 MB per frame at 15 fps).
  2. In the camera callback call `previewEncoder.offer(...)` **before** `exec.submitLatest {}` and remove the preview block from the inference task; inference no longer pays for encoding and preview is no longer dropped when inference is busy.
  3. `EventUploader.previewMinIntervalMs` becomes a constructor/`setPreviewMinInterval` value driven by policy (keep the backpressure drop on `queueSize() > maxQueueBytes`; add a counter exposed in telemetry `preview_dropped_backpressure`).
  4. Adaptive quality: step the policy down one rung (fps -> quality -> max_edge) when `ThermalGovernor` level degrades or backpressure drops exceed 20% in 10 s; step back up after 30 s clean. Rungs (initial): `{5 fps, 640, q75}` -> `{3, 640, q65}` -> `{2, 480, q60}` -> `{1, 320, q60}`.
  5. Defaults: focus 3 fps / 640 px / q70; grid 0.5 fps / 240 px / q50 (as T4.3). The analysis camera is 640x480 so 640 is full native resolution; anything larger needs T5.3.
- **Tests**: `PreviewEncoderTest` (pure JVM: fake clock and a stub encode function): newest-frame-wins, interval honoured, parameter change applies to the next frame, encode failure does not throw into the camera thread; `ThermalGovernorTest`-style test for the rung stepping logic extracted as a pure `PreviewLadder`.
- **Measure** with T5.1's script before and after; target: >= 3 fps and 640 px with no increase in `ThermalGovernor` level over 10 minutes on the A07 and unchanged inference latency (compare `Metrics` inference ms).
- **DoD**: android tests green; measurement table in `docs/BASELINE_2026-10-10.md`.

## T5.3 Optional: higher-resolution preview stream (only if 640 px is not enough)
- Evaluate a second CameraX use case (`ImageCapture` or a second `ImageAnalysis` at 1280x720) for preview only. Check Camera2 concurrent stream limits on the A07 via `adb shell dumpsys media.camera` (`Camera 0 static info`), measure temperature and battery over 10 min, and compare with simply upscaling the sharpened 640 px frame. Decision memo in `docs/BASELINE_2026-10-10.md`; merge only if thermal level stays unchanged.

## T5.4 Optional: WebRTC feasibility (separate spike, no production code)
- Questions: STUN/TURN need on Render, `aiortc` vs an SFU (LiveKit/mediasoup) hosting, Android WebRTC library size vs the current 67 MB release APK, battery cost vs JPEG, and Render free-tier egress (verify the actual limit in the Render dashboard). Output: a one-page recommendation; most likely outcome is "stay on JPEG, raise limits".

---

# Phase 6: battery stats and debug mode

## T6.1 Telemetry uplink (Android + backend)
- **Message** (plain JSON envelope like `treat`/`ping`, not a pydantic contract unless you choose to add one in all three mirrored files):
  ```json
  {"type":"telemetry","data":{"ts":<phone secs>,"battery_pct":93,"charging":true,"plugged":"usb","temp_c":31.9,
    "thermal_status":0,"thermal_headroom":null,"inference_ms":{"detector":12.1,"pose":18.4,"face":9.0,"yamnet":3.2},
    "preview_kbps":48.0,"preview_fps":3.1,"preview_dropped":2,"ws_rtt_ms":41,"queue_bytes":0,"spool_bytes":0,
    "mem_mb":212,"degrade_level":"NORMAL"}}
  ```
- **Android**: new `capture/TelemetrySampler.kt` run every 10 s on the service scope. Battery from the sticky `Intent.ACTION_BATTERY_CHANGED` (`EXTRA_LEVEL/SCALE`, `EXTRA_STATUS`, `EXTRA_PLUGGED`, `EXTRA_TEMPERATURE` in tenths of a degree C) via `registerReceiver(null, IntentFilter(...))`; thermal via the existing `AndroidThermalProvider`; inference timings from `bench/Metrics.kt` (read it to reuse the field names), RTT from the ping/pong round trip (`EventUploader` already sends pings every 15 s: record `pongAt - pingAt`; add a `lastRttMs`), memory from `ActivityManager.MemoryInfo`/`Debug.getNativeHeapAllocatedSize`. `EventUploader.sendTelemetry(json)` (no spool; drop when disconnected).
- **Backend**: `RemoteSession.handle_text` branch `telemetry`: validate numeric ranges (battery 0-100, temp -20..90, ms values 0..60000; drop the envelope on violation), keep the latest dict and a ring buffer (`deque(maxlen=360)`, 1 h at 10 s) on the session, expose `latest_telemetry` in `snapshot()` (phone card) and `metrics()`; `RemotePipeline.status()` adds `battery_pct`, `charging`. Add the fields to `frontend/lib/types.ts` `PhoneStatus` (`battery_pct?`, `charging?`, `temp_c?`).
- **Tests**: Kotlin `TelemetrySamplerTest` with an injected battery provider; Python `RemoteSession` accepts valid, rejects out-of-range and non-numeric values; ring buffer bound; snapshot contains the latest values; type-sync tests unchanged/green.
- **Phone check**: `/status.phone.battery_pct` equals `adb shell dumpsys battery` level within 1 point; unplug USB and see `charging:false` within 15 s.

## T6.2 Dashboard battery UI
- Battery pill per station (icon + %, charging bolt, colour at <=20%), shown in the compact header (T1.6 slot) and on each `StationTile`. Toasts via `lib/toasts.ts` at 20% and 10% when not charging (once per crossing, reset when charging or above 25%). Stale telemetry (no sample for 45 s) shows "?" instead of a value.
- **Tests**: `lib/battery.ts` pure functions (`batteryLevelClass`, `shouldToast(prev, next, charging)`) with Vitest.

## T6.3 Config-driven debug mode (server and dashboard)
- **Config**: `web.debug.enabled` (default false), env `BTB_DEBUG=1`, `web.debug.history_s` (default 3600). Advertised to the dashboard in `/auth/status` (`debug: bool`) so the UI knows whether to render the drawer; owner-only (`role == owner`), 404 when disabled.
- **Endpoint** `GET /debug/metrics?station=&since=`: returns `{server: {rss_mb, cpu_pct, threads, uptime_s, loop_lag_ms}, hub: {clients, queue_depths}, households: n (legacy/pin: 1), station: {latest_telemetry, series: [...]}, downlinks: {sent, dropped}, drain_pct_per_h}`. Server stats via `psutil` (add to `requirements-web.txt` and install with `uv pip install --python .venv/bin/python psutil`; `psutil` is not currently installed and the venv has no `pip`); `loop_lag_ms` from a small asyncio task measuring sleep overshoot; series from the T6.1 ring buffers; `drain_pct_per_h` from a linear fit over the last 30 min of unplugged samples (null while charging).
- **Dashboard**: `components/DebugDrawer.tsx` opened from a gear icon (only when `debug` is true): sparklines (reuse `Sparkline.tsx`) for battery, temperature, inference ms per stage, preview kbps/fps, RTT, backend CPU/RSS; a table of the latest raw values; a "copy diagnostics JSON" button. The developer-only chips removed from the main UI in T1.6 (LLM provider/model, clock offset, N fps) render here.
- **Tests**: `/debug/metrics` 404 when disabled, 403 for viewers, shape/limits when enabled (`psutil` monkeypatched), `drain_pct_per_h` unit test with synthetic series; Vitest for the series downsampling helper.
- **DoD**: green; screenshot of the drawer at 390 px with the real phone connected.

## T6.4 Resource budget alerts (small, optional)
- Server-side rules: log and publish a `status` warning when `temp_c >= 42`, `thermal_status >= 3`, `battery_pct <= 15 and not charging`, or `preview_dropped` grows > 20% over 60 s; dashboard shows a toast. Pure functions + tests.
