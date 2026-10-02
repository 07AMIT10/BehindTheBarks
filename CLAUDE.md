# CLAUDE.md — Dog Emotion Monitor (feeding-zone demo)

## What this project is

A web app that watches a dog through a single static camera (plus a microphone) pointed at a
constrained feeding area. It locates the dog, reads posture, face and vocalisations, infers an
emotional state (happy, anxious, etc.), shows it on a live dashboard and pushes a notification to
the owner when the state meaningfully changes.

Build window: 2 days. Demo: live camera, with a pre-recorded fallback that must work offline.

## Non-negotiables for anyone (human or Claude) working in this repo

- **Do not train pose or detection models from scratch.** Use pretrained weights. The only training
  that is in scope is an optional fine-tune of a facial-landmark regressor on DogFLW.
- **The fallback demo must run with no network.** Every live dependency (LLM API, Telegram) has a
  cached or stubbed path in demo mode.
- **Components talk only through the JSON contracts below.** Change a contract only after agreeing on it
  with whoever consumes it, and update this file in the same commit.
- **Emotion labels come from the fixed vocabulary below.** Nothing else reaches the dashboard.
- **The only native mobile app allowed is the on-device event producer** (`android/`: a Kotlin Android app that
  runs the whole perception stack on the phone and streams the JSON contracts below to `/ingest-events`).
  It may not change a contract, and while it exists the browser `/camera` page (`/ingest`, server inference)
  and the server pipeline stay in place as the fallback path — neither may be deleted in the same phase.
  Notifications still go through a Telegram bot (web push only if time allows).
- Feature freeze: early afternoon of day 2. After that, only bug fixes and rehearsal.

## Architecture

```
            ┌───────── video source (browser | webcam | stream | file) ─────────┐
            ▼                                                                    │
  dog detection (YOLO, COCO "dog") ─► body keypoints (pretrained quadruped pose) │
            │                                  │                                 │
            └─► face crop ─► face landmarks ───┤                                 │
                                               ▼                                 │
                                     feature extraction ─► FrameEvent            │
                                                                                 │
  audio source (browser | mic | file) ─► YAMNet ─► AudioEvent                    │
                                                                                 │
  FrameEvent + AudioEvent ─► fusion (rules + LLM interpreter) ─► EmotionState ─► dashboard
                                                                        └─► notifier (Telegram)
```

## Tech stack (draft: confirm before building)

- **Backend:** Python 3.11, FastAPI, WebSocket push to the dashboard
- **Vision:** Ultralytics YOLO (detection), DeepLabCut SuperAnimal-Quadruped or MMPose AP-10K
  (body keypoints), DogFLW-based landmark model (face: pretrained 46-point TFLite from
  `hugocornellier/dog-face-landmarks` on Hugging Face, fetched once by `scripts/fetch_face_model.py`;
  weights are CC BY-NC 4.0, non-commercial, not committed)
- **Audio:** YAMNet via TensorFlow Hub, stock AudioSet classes
- **Interpreter:** provider-agnostic LLM layer over an OpenAI-compatible API (Groq or OpenRouter,
  chosen by config). No provider or model is hard-coded (see "LLM interpreter" below)
- **Frontend:** Next.js + React dashboard
- **Notifications:** Telegram Bot API

## Repo layout

```
/backend
  main.py              FastAPI app, WebSocket endpoint, event log
  sources.py           input abstraction: browser | webcam | stream | file (one config flag)
  /vision
    detect.py          YOLO dog detection + crop
    pose.py            body keypoints
    face.py            face landmarks
    keypoint_map.py    maps model-specific keypoint names to our canonical names
    features.py        FrameEvent feature extraction
  /audio
    yamnet_events.py   AudioEvent stream
  /fusion
    rules.py           heuristic emotion scoring
    llm_interpreter.py   provider-agnostic interpreter (Groq | OpenRouter)
    state.py           state machine, debouncing, cooldowns
    prompts.py         LLM system prompt + request builder (iterate wording here)
    llm_parse.py       defensive LLM reply parsing
    llm_triggers.py    when to call the LLM (triggers, rate limit)
  /notify
    base.py            Notifier port, caption format
    dashboard.py       dashboard-only notifier ("would send to owner")
    telegram.py
  /web                 Web runtime (Person B): settings.py, hub.py (WS fan-out), event_log.py, runtime.py (wiring)
    ingest.py          /ingest browser-phone protocol (hello, binary dispatch, fps meter)
    ingest_events.py   /ingest-events on-device phone protocol (JSON envelopes, clock rebasing)
    remote_pipeline.py RemotePipeline: the Pipeline interface for web.pipeline: remote
  /demo
    mock_pipeline.py   scripted MockPipeline (same interface as Pipeline)
    cache/             pre-computed LLM responses for fallback clips
/frontend              Next.js dashboard + /camera page (phone as camera)
/android               Kotlin Android app: perception on-device, streams events to /ingest-events
  app/src/main/assets/models/   git-ignored; filled by scripts/fetch_android_models.py (weights, never committed)
/data
  fallback/            stock clips + audio used for the offline demo
/config.yaml           source, thresholds, cooldowns, demo_mode flag
```

## Datasets and what each is for

| Dataset | Contents | Our use |
|---|---|---|
| StanfordExtra (WLDO, ECCV 2020) | 2D body keypoints + silhouettes, ~12k images, 120 breeds | Validation of the pose stage; keypoint naming reference |
| DogFLW (Kaggle) | Dog facial landmarks in the wild | Optional fine-tune of the face landmark model; validation |
| YAMNet / AudioSet | Pretrained audio classifier | Used as-is. Relevant classes: `Dog`, `Bark`, `Yip`, `Howl`, `Bow-wow`, `Growling`, `Whimper (dog)` |
| ESC-50 (dog class) / AudioSet clips | Labelled dog audio | Audio for the fallback demo |

**Important:** none of these datasets has emotion labels. Emotion is inferred by `fusion/`, not learned
from a dataset.

## Fallback footage

We cannot record our own video. The fallback therefore uses:
- freely licensed stock dog clips (Pexels, Pixabay), chosen to resemble a static camera over a feeding
  area. Aim for 3–5 clips covering eating, excitement at a treat, idle/disinterested and one anxious or
  vocal moment
- dog audio from ESC-50 / AudioSet, mixed onto clips where the original sound is unusable
- licence and source URL recorded for every file in `data/fallback/SOURCES.md`

In demo mode, every LLM response for these clips is pre-computed and stored in `backend/demo/cache/`.

### Fallback pack (Data produces, Web replays)
```
data/fallback/
  clips/<name>.mp4          H.264 + AAC, constant 30 fps (scripts/mux_audio.py)
  events/<name>.jsonl       one envelope per line, same as the WebSocket; ts = seconds from clip start
  events/<name>.debug.mp4   annotated video for tuning (not committed)
  manifest.json
```
`manifest.json` (paths relative to its folder; `treats` and `expected_emotion` are filled by hand, the rest
by `scripts/precompute_events.py`):
```json
{ "version": 1, "ts_origin": "clip_start",
  "clips": [ { "name": "treat_drop", "clip": "clips/treat_drop.mp4", "events": "events/treat_drop.jsonl",
               "duration_s": 24.3, "frames": 194, "treats": [4.0, 15.5], "expected_emotion": "excited",
               "observed": { "dominant": "excited", "seconds": { "excited": 9.1, "relaxed": 12.0 } },
               "notes": "" } ] }
```
In `events/*.jsonl` every `ts` (frames, audio, rules, treat) is seconds from the clip start, not wall time:
replay by adding your own start time. `treats` are in the same seconds, and each has a `treat` line in the
events file. `expected_emotion` is `null` until filled and is otherwise from the emotion vocabulary.

## Phone camera (primary live source)

The live demo uses a **phone as the camera and microphone**, running our own web page in its browser.
Backup live source: a phone IP-camera app feeding the `stream` source (no extra code).

- **Page:** `/camera` in the Next.js app (Person B). Captures camera + mic with `getUserMedia`, shows a
  feeding-zone guide for positioning, keeps the screen awake, streams to the backend.
- **Transport:** WebSocket `/ingest` on the backend (Person B). One phone at a time.
  - First message, JSON text: `{"type": "hello", "width", "height", "fps", "sample_rate", "device"}`
  - Then binary messages. First byte is the kind:
    `0x01` + JPEG bytes (long side ~640 px, quality ~0.7, at the configured fps) ·
    `0x02` + mono PCM, Int16 little-endian, at the hello's `sample_rate`, ~100 ms per chunk
- **Handoff:** Web calls `pipeline.ingest_frame(jpeg, ts)` and `pipeline.ingest_audio(pcm16,
  sample_rate, ts)` with `ts = time.time()` **at receipt on the server**, so video and audio share the
  same clock as every other source. Data decodes, resamples to 16 kHz and feeds the normal pipeline.
- **Source type:** `browser` in config. Frames may be portrait; nothing may assume landscape.
- **HTTPS is mandatory:** browsers only allow camera/mic on HTTPS (or localhost). For the demo, run one
  `cloudflared` quick tunnel per port (frontend and backend); the camera page takes the backend's
  `wss://` URL from `NEXT_PUBLIC_BACKEND_URL` or a `?backend=` query parameter.
- **Hello extensions (optional):** `facing` (`back` | `front`), `camera` (false = mic-only after the user denied camera permission; the dashboard shows the camera-blocked card instead of stalling).
- **/ingest close codes:** `4400` bad hello, `4408` replaced by a newer phone after going quiet, `4409` another phone is already streaming.
- **Disconnects:** no frame for > 2 s → the source reports `stalled`, the rules go to `unknown`, and
  everything recovers automatically when frames resume. No restart needed.
- **On-device variant:** when the perception runs on the phone instead of the backend, see
  **On-device phone (event sink)** below.

## On-device phone (event sink)

The on-device path inverts the division of labour: an Android app runs detection → tracking → pose →
face → audio → features → rules on the phone and the backend only receives the events it already
speaks. The JSON contracts below do not change, so the dashboard, the LLM, the notifier and demo mode
are untouched; only the producer of the envelopes moves. (Plan:
   `docs/superpowers/plans/2026-10-02-android-ondevice-v2.md`. The "no native mobile app"
   non-negotiable above was amended to allow exactly this app, as an event producer and nothing else.)
- **Runtimes stay CPU-only.** LiteRT drags in vendor NPU libraries (Google `libedgetpu_litert.so`,
  Qualcomm `libcdsprpc.so`, MediaTek `.mtk.so`) as unused manifest declarations; they are never loaded,
  and the app must select the accelerator explicitly (CPU/XNNPACK), per "no NNAPI, no NPU code".

- **Server role:** `web.pipeline: remote` (`backend/web/remote_pipeline.py`) — a `Pipeline`
  implementation with **no models, no source and no watchdog**. Events arrive over the socket, preview
  JPEGs feed `/video` and notification snapshots, and `status()` mirrors the phone's own metrics.
  `WEB_PIPELINE=remote` or `make dev-backend-remote` selects it; `mock` and `real` are unchanged.
- **Transport:** WebSocket `/ingest-events` on the backend (`backend/web/ingest_events.py`). One phone
  at a time, shared with `/ingest` (a browser `/camera` phone and an on-device phone cannot run
  together), and the same close codes as `/ingest`.
  1. text hello: `{"type": "hello", "proto": 1, "device", "phone_time": <epoch s>, "models": {...},
     "profile": "mobile"}` (`models`, `profile` and `proto` are optional; `phone_time` is seconds).
     The server replies `{"type": "hello_ack", "server_time": <epoch s>}`.
  2. text envelopes, the same `{"type": "frame" | "audio" | "rules" | "treat", "data": {...}}` shape
     as `events/*.jsonl` and the dashboard feed. `data` is validated with the pydantic contract, so an
     unknown key, a missing `ts` or a label outside the vocabulary is **counted in `status.phone.dropped`
     and thrown away** — one bad envelope never closes the socket. A callback inside the Runtime that
     raises (a full disk in the event log, for instance) costs that one envelope too, and the counters
     only move for envelopes that were actually dispatched.
  3. text `{"type": "ping", "phone_time": <epoch s>}` every `web.remote.ping_every_s` (30 s) →
     `{"type": "pong", "server_time": ...}`. This keeps the clock offset fresh.
  4. binary `0x01` + JPEG preview (≤ 2 fps, ~320 px long side, quality ~0.6). Stamped at server receipt
     like `/ingest`; it feeds `pipeline.latest_frame_jpeg()`, not the rules.
- **Clocks:** `data.ts` is the phone's clock and every other timer (`no_dog_unknown_s`, `llm_stale_s`,
  cooldowns) compares against the server's. The server rebases `ts` by
  `server_time_at_hello − phone_time`, refreshed by each ping as the **median of the last
  `web.remote.clock_samples` (5) samples**, so one late ping cannot shift the whole stream. An envelope
  whose rebased `ts` is further than `web.remote.max_skew_s` (60 s) from server receipt is counted as a
  drop, not trusted: a buggy app sending milliseconds or a typo'd year would otherwise freeze
  `no_dog_unknown_s`, `llm_stale_s` and the stall detector and leave the dashboard "live" for ever.
  Raise `max_skew_s` (or re-stamp `ts` on flush) if the app ever spools events across a longer outage.
- **Downlinks** (server → phone, on the same socket): `{"type": "treat", "data": {"ts",
  "offset_s"}}` whenever the treat button fires, so the phone's rules see `treat_event_recent`.
  **`data.ts` is on the phone's clock**, not the server's — the app must compare it against its own
  clock, which is the whole point of the handshake — and `data.offset_s` (`server − phone`) is sent
  with it so the app can verify the conversion; rebase its own clock by `offset_s` if it prefers to
  stay on one clock. The phone must **not** re-send a treat it received: the phone's own `treat`
  envelope comes back down as the downlink and would loop. `{"type": "config", "data": {"rules":
  {...}}}` comes from `POST /phone/config` (with no body it sends the server's `data.rules`), so
  retuning rules needs no APK rebuild. Each send has a `web.remote.downlink_timeout_s` (5 s) deadline:
  a half-open socket is counted in `pipeline_status.downlinks_dropped` and the queue drains, so one
  dead connection cannot silently swallow later treats and config pushes.
- **/ingest-events close codes:** same as `/ingest` — `4400` bad hello, `4408` replaced by a newer
  phone after `web.ingest.stale_s` of silence, `4409` another phone is already streaming. Close code
  `1000` (the app's own Stop) forgets the phone; anything else leaves a disconnected card.
- **Stalling:** no FrameEvent or preview for `web.remote.stale_s` (2 s) → `pipeline.status().state`
  is `stalled`. There is no watchdog to inject "no dog" frames, so the Runtime's own no-dog path takes
  the label to `unknown` after `web.state.no_dog_unknown_s` and recovers by itself when events resume.
- **Status card:** `status.phone` gains `transport: "ingest-events"`, `profile`, `proto`, `models`,
  `events`/`previews`/`treats`/`pings`/`dropped`, `preview_fps`, `clock_offset_s`, `clock_samples` and
  `ping_due_s` (negative = the phone is overdue or half-open).
- **Fallbacks, in order:** Android app → `/ingest-events`; browser `/camera` page → `/ingest`
  (unchanged, server inference); `DEMO_MODE=1` offline clips (unchanged).

## Emotion vocabulary (fixed)

`happy` · `excited` · `relaxed` · `anxious` · `fearful` · `aggressive` · `disinterested` · `unknown`

## JSON contracts

### FrameEvent (vision → fusion), ~5–10 per second
```json
{
  "ts": 1727340000.123,
  "source": "live",
  "dog_detected": true,
  "bbox": [x1, y1, x2, y2],
  "bbox_conf": 0.91,
  "body_keypoints": { "tail_base": [x, y, conf], "tail_tip": [x, y, conf] },
  "face_landmarks": [[x, y], ...],
  "features": {
    "tail_height": 0.4,
    "tail_wag_hz": 2.1,
    "ear_position": "up",
    "mouth_open": 0.6,
    "body_lowering": 0.1,
    "motion_energy": 0.7,
    "in_feeding_zone": true
  }
}
```
Ranges: `tail_height` −1 (tucked) to 1 (high). `mouth_open`, `body_lowering` and `motion_energy`
run from 0 to 1. `ear_position` is `up | neutral | back | unknown`. Any feature may be `null` if
keypoints are missing or low confidence (< 0.3).

### AudioEvent (audio → fusion), debounced from YAMNet windows (~0.96 s, 0.48 s hop)
```json
{ "ts": 1727340000.5, "label": "bark", "score": 0.82 }
```
`label` is `bark | yip | growl | whimper | howl | silence | other`.
`ts` is the end of the window. Events are emitted on onsets and changes, not every window: a dog
label at most once per `data.audio.debounce_s` (default 1 s), `silence`/`other` only when they differ
from the previous window. Consumers must not assume a steady rate; the current sound is the latest
event. `debounce_s: 0` gives one event per window.

### EmotionState (fusion → dashboard / notifier)
```json
{
  "ts": 1727340001.0,
  "emotion": "excited",
  "confidence": 0.78,
  "source": "fused",
  "reason": "High, fast tail wag and open mouth as the treat drops; two short yips.",
  "snapshot": "base64 or URL"
}
```
`source` is `rules | llm | fused`.

## Fusion logic

### Rules (first pass, tune against fallback clips)
- **happy / relaxed:** tail neutral-to-high with moderate wag, mouth open and loose, ears neutral, no growl
- **excited:** high, fast tail wag, high motion energy, yips or short barks, often at a treat event
- **anxious:** low tail, ears back, body lowering, whimpers, pacing (high motion without approaching food)
- **fearful:** tail tucked, ears flat back, strong body lowering, retreating from the zone
- **aggressive:** growl + stiff body, ears forward, mouth tension (treat with caution; low confidence by default)
- **disinterested:** in the feeding zone but low motion, not eating, for longer than N seconds (appetite flag)

Rules output a score per emotion. Top score becomes the rules label.

### LLM interpreter
The inference layer is **provider-agnostic**. Groq and OpenRouter both expose OpenAI-compatible
chat-completions endpoints, so `llm_interpreter.py` uses one client (the `openai` Python SDK with a
custom `base_url`) and reads everything from config:

| Setting | Groq | OpenRouter |
|---|---|---|
| `LLM_BASE_URL` | `https://api.groq.com/openai/v1` | `https://openrouter.ai/api/v1` |
| `LLM_MODEL` | any model on Groq's model list | any model slug on OpenRouter |

Rules for this layer:
- **Never hard-code a provider or model name** anywhere except `config.yaml` / env vars.
- Prefer a **vision-capable** model so the frame can be sent. If `LLM_VISION=0` (the chosen model is
  text-only), send the features and audio events without the image. The pipeline must work both ways.
- Swapping provider or model must need **no code change**, only config. Test both providers before the freeze.
- Timeout: 8 s per call. On timeout or error, fall back to the rules label and log it. Never block the
  video loop: run calls off the main loop (async task or worker thread).
- Log provider, model and latency on every call, so options can be compared on day 2.

Called when any of these happen: a treat event, an AudioEvent with score > 0.6, a rules-label change,
or every 10 s as a heartbeat. Never more than one call every 3 s (adjust to the provider's rate limits).

Input: one JPEG frame (dog crop plus context, sent as a base64 `image_url` content part) when vision is
on, the last ~3 s of `features`, recent AudioEvents and the current rules label.

The system prompt must require **JSON only**, no prose and no code fences. Request JSON mode
(`response_format: {"type": "json_object"}`) where the model supports it, but never rely on it:
```json
{ "emotion": "<vocabulary>", "confidence": 0.0-1.0, "reason": "<one sentence, observable cues only>" }
```
Parse defensively: strip fences, validate against the vocabulary, clamp confidence, and fall back to the
rules label on any failure.

### Final label
If the LLM and rules agree → `fused`, confidence = mean. If they disagree → take the LLM's label only if
its confidence ≥ 0.7, otherwise the rules label. If no dog is detected for > 2 s → `unknown`.

## Notifications
- Fire only on a **state change** that persists for ≥ 3 s.
- Cooldown per emotion: 60 s (configurable).
- Always notify on `fearful`, `aggressive` and `disinterested` beyond threshold, subject to cooldown.
- Message: emotion, one-line reason, snapshot.

## Dashboard
- Live video with bounding box + skeleton overlay
- Current emotion, confidence and reason
- Timeline of emotion states for the session
- Audio event strip (barks, whines)
- Toggle: live / fallback clip
- Phone camera page (`/camera`), mobile-first; the dashboard itself works on mobile and desktop

## Commands

Data side (Person A):
```
# one-time setup
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-data.txt
python scripts/check_env.py                 # verify torch/MPS, tensorflow, ultralytics etc. actually import
python scripts/fetch_face_model.py          # downloads the DogFLW TFLite model once (needs network)

# run the pipeline directly (bypassing Web's FastAPI app)
python scripts/run_pipeline.py --source file --path data/fallback/clips/eating_home.mp4 --fast \
    --jsonl out/events.jsonl --debug-video out/debug.mp4 --treat-at 6
python scripts/run_pipeline.py --source browser --path some_clip.mp4 --portrait   # simulate a phone
python scripts/run_pipeline.py --source webcam --duration 30

# fallback pack (data/fallback/): source clips into raw/ yourself, then
python scripts/sources_stub.py                          # stub SOURCES.md rows for new raw/ files
python scripts/mux_audio.py raw/x.mp4 --add SOUND@4.5    # overlay audio, write to clips/
python scripts/precompute_events.py                      # build events/*.jsonl + manifest.json
python scripts/tune.py                                    # per-clip timeline plots + match table

pytest                                       # full data-side test suite
```

Web side (Person B):
```
# web backend (venv: uv pip install --python .venv/bin/python -r requirements-web.txt)
make dev-backend            # uvicorn backend.main:app --reload --port 8000 (web.pipeline: mock | real)
make dev-backend-remote     # same, but WEB_PIPELINE=remote: the phone produces the events (/ingest-events)
make test-web               # pytest tests/web
python scripts/llm_smoke_test.py [frame.jpg]   # one real LLM call (reads .env)
python scripts/telegram_test.py                # one test photo to Telegram
make demo                     # DEMO_MODE=1 backend (offline fallback path; frontend: npm run start)
python scripts/check_integration.py --seconds 30   # live-pipeline checklist (Step 10)
python scripts/llm_benchmark.py --frames <dir> --n 20  # provider comparison (Step 11)
python scripts/soak.py --minutes 20                    # leak + stall check (Step 11)
# frontend (Next.js 16; first time: cd frontend && npm install)
make dev-frontend           # http://localhost:3000 (backend URL: ?backend=… or NEXT_PUBLIC_BACKEND_URL)
make test-frontend          # vitest + typecheck + lint
python scripts/gen_ts_types.py   # after changing backend/contracts.py
# HTTPS for the phone camera (one quick tunnel per port)
cloudflared tunnel --url http://localhost:3000
cloudflared tunnel --url http://localhost:8000
# demo mode (offline, fallback clips)
DEMO_MODE=1 uvicorn backend.main:app
```

On-device phone (`android/`, needs `ANDROID_HOME` = the Android SDK root):
```
# one-time setup: install platform-tools, platforms;android-35, build-tools;35.0.0 and accept licences
sdkmanager --sdk_root="$ANDROID_HOME" --licenses
make android-build      # ./gradlew :app:assembleDebug, then zipalign -c -P 16 -v 4 (16 KB page size)
make android-test       # ./gradlew :app:testDebugUnitTest
make android-install    # ./gradlew :app:installDebug (phone over USB with USB debugging on)
```

## Environment variables
`LLM_PROVIDER` (`groq` | `openrouter`), `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_VISION` (`1` | `0`), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DEMO_MODE`,
`WEB_PIPELINE` (`mock` | `real` | `remote`, overrides `web.pipeline`)

## Team and ownership (2 people)

The split follows one boundary: **Data produces events, Web consumes them.** Everything left of
the JSON contracts belongs to Data; everything right of them belongs to Web. Neither person edits
the other's folders without asking.

### Person A — Data (models, signals, rules)
Owns: `backend/sources.py`, `backend/vision/`, `backend/audio/`, `backend/fusion/rules.py`, `data/`

- Input abstraction: browser | webcam | stream | file, selected by one config flag; the browser
  source receives phone frames/audio through `Pipeline.ingest_*`
- Dog detection, body keypoints, face landmarks, `keypoint_map.py`
- Feature extraction into `FrameEvent`, YAMNet into `AudioEvent`
- Rules v1 and tuning (the person who understands the features writes the rules)
- Sourcing fallback clips and audio, recording licences in `data/fallback/SOURCES.md`
- Optional DogFLW fine-tune, only if a pretrained face-landmark model can't be found by midday day 1
- **Deliverable for Web:** a `Pipeline` class (below) plus a pre-computed `events.jsonl` for every
  fallback clip, so Web can build and demo against real data

### Person B — Web (backend, LLM, dashboard, alerts)
Owns: `backend/main.py`, `backend/fusion/llm_interpreter.py`, `backend/fusion/state.py`,
`backend/notify/`, `backend/demo/`, `frontend/`, `config.yaml`, env setup

- FastAPI app, WebSocket push, event log, `/video` MJPEG endpoint from the pipeline's latest frame
- LLM interpreter (Groq / OpenRouter switch), final-label logic, state machine, cooldowns
- Telegram notifications
- Phone camera page (`/camera`) and the `/ingest` WebSocket, HTTPS tunnels for the demo
- Dashboard: live video with skeleton overlay drawn on a canvas from keypoints, current emotion,
  timeline, audio strip, live/fallback toggle, **"treat dropped" button**
- Demo mode: replay `events.jsonl` + clip, serve cached LLM responses, zero network
- **Mock first:** write `mock_events.py` from the contracts in hour one, and build everything against
  it until Data's real events arrive

### The handoff interface (agree on this before splitting up)
```python
class Pipeline:
    def __init__(self, config): ...
    async def run(self, on_frame_event, on_audio_event, on_rules_label): ...
    def latest_frame_jpeg(self) -> bytes | None: ...
    def mark_treat(self, ts: float) -> None: ...                                  # treat button
    def ingest_frame(self, jpeg: bytes, ts: float) -> None: ...                  # phone camera
    def ingest_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None: ...  # phone mic
    def status(self) -> dict: ...
    def stop(self): ...
```
`on_rules_label` receives `{"ts", "emotion", "confidence", "scores": {emotion: score}}`.
`mark_treat()` sets the rules engine's `treat_event_recent` flag for `data.rules.treat_window_s`
(default 10 s). `ingest_*` are non-blocking, never raise, drop the oldest data when behind, and are
no-ops (with one warning) unless the source type is `browser`. `status()` returns
`{"source", "state": "running" | "stalled" | "stopped", "fps", "last_frame_age_s", "audio_ok"}`
(those five keys always; `RemotePipeline` adds the phone's own counters on top, and there
`mark_treat()` pushes a downlink instead of setting a local flag — see "On-device phone").
Web calls only these methods and never imports anything else from `vision/` or `audio/`.

`scripts/run_pipeline.py --jsonl` (and the precomputed `events.jsonl` per fallback clip) writes one
line per event in the same envelope as Web's WebSocket: `{"type": "frame" | "audio" | "rules" | "treat",
"data": {...}}`, where `data` is the FrameEvent / AudioEvent / RulesLabel JSON (or `{"ts"}` for a treat).
In `--fast` mode lines are in timestamp order. `FrameEvent.source` is `"live"` for browser, webcam and
stream sources and `"file"` for clips.

## Two-day plan

| When | Person A — Data | Person B — Web |
|---|---|---|
| Day 1, hour 1 | **Together:** freeze JSON contracts + `Pipeline` interface | **Together** |
| Day 1 morning | Source abstraction, YOLO + pretrained pose on a stock clip, YAMNet events | FastAPI + WebSocket, `mock_events.py`, dashboard skeleton, Telegram test message |
| Day 1 afternoon | Face landmarks, features, rules v1, source 3–5 fallback clips | LLM interpreter with provider switch, state machine, cooldowns, video endpoint + overlay |
| Day 1 evening | First real `events.jsonl` handed over | **Together:** one fallback clip end to end, real events replacing mocks |
| Day 2 morning | Tune rules on all fallback clips, fix missing/low-confidence keypoints | Timeline, audio strip, treat button, cache LLM responses for demo mode, compare providers |
| Day 2 early afternoon | **Feature freeze** | **Feature freeze** |
| Day 2 rest | **Together:** live test in venue, rehearse the fallback switch twice, fix only what breaks | **Together** |

If Data falls behind, Web does **not** wait: the demo can run on mocks plus the LLM reading raw frames,
and Data's real signals slot in when ready. If Web falls behind, cut the audio strip and timeline first;
the live emotion card and one notification are the core of the demo.

## Open questions
- Phone placement and mount near the bowl (decided: phone browser is the live camera, IP-camera app as backup)
- How treat events are detected (recommended: a manual "treat dropped" button on the dashboard)
- Default provider and model for the demo (Groq vs. OpenRouter), decided from day-2 latency logs