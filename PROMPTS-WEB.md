# Claude Code prompts — Person B (Web)

Run these **in order**, one prompt per step. Commit after each step passes its checks, then `/clear`
before the next prompt so every step starts from CLAUDE.md and the repo rather than a stale context.
For prompts marked **[plan first]**, switch to plan mode (Shift+Tab) and approve the plan before any
code gets written.

> **Revision:** the live camera is now a phone browser. A new **Step 8b (phone camera page and
> /ingest)** has been added; no other step numbers changed. If you already did Step 1 or Step 2, add
> the new Pipeline methods (`ingest_frame`, `ingest_audio`, `status`) to the shared interface and to
> MockPipeline as part of Step 8b. Pull the updated CLAUDE.md first.

Every prompt assumes CLAUDE.md is at the repo root. It is the shared source of truth with Person A.
Until Person A's real events arrive (planned for the evening of day 1), **everything runs on mocks**.

---

## Step 0 — Bootstrap the web side  **[plan first]**

```
Read CLAUDE.md fully. I am Person B (Web). I own backend/main.py, backend/fusion/llm_interpreter.py,
backend/fusion/state.py, backend/notify/, backend/demo/, frontend/, config.yaml and env setup. Do not
create or edit Person A's areas (backend/sources.py, backend/vision/, backend/audio/,
backend/fusion/rules.py, data/), except that backend/contracts.py and backend/pipeline.py are shared.

Set up:
1. Python 3.11 env and requirements-web.txt (fastapi, uvicorn[standard], pydantic v2, openai,
   httpx, python-dotenv, pyyaml, opencv-python-headless for frame extraction, pytest, pytest-asyncio).
   Pin versions.
2. Next.js app in frontend/ with TypeScript and Tailwind. Keep it lean: no auth, no database.
3. .env.example with every variable from the "Environment variables" section of CLAUDE.md, and make
   sure .env is gitignored.
4. config.yaml with a web section (LLM call triggers, min interval, timeout, cooldowns, persistence
   threshold, demo settings). If Person A already created config.yaml, only add my section.
5. A Makefile or npm scripts: `dev-backend`, `dev-frontend`, `dev` (both), `demo`.

Done when both dev servers start and the Next.js page loads a "backend: connected" indicator from a
FastAPI /health endpoint.
```

---

## Step 1 — Contracts (do this together with Person A)

```
Read CLAUDE.md, "JSON contracts" and "The handoff interface".

If backend/contracts.py and backend/pipeline.py don't exist yet, create them exactly as specified
(pydantic v2 models: FrameEvent with nested Features, AudioEvent, RulesLabel, EmotionState; the
Emotion literal; Pipeline with run, latest_frame_jpeg, mark_treat, ingest_frame, ingest_audio,
status, stop). If they exist, diff them
against CLAUDE.md and report mismatches instead of overwriting.

Then generate matching TypeScript types in frontend/lib/contracts.ts. Add a small script,
scripts/gen_ts_types.py, that regenerates them from the pydantic models' JSON schema, so the two
never drift.

Tests: each model round-trips through JSON; the TS types compile.
```

---

## Step 2 — Mock pipeline

```
Read CLAUDE.md and backend/pipeline.py.

Create backend/demo/mock_pipeline.py: a MockPipeline class with the SAME interface as Pipeline that
emits realistic fake data. It plays a scripted 90-second loop:
relaxed idle -> treat dropped -> excited (fast tail wag, yips) -> happy eating -> disinterested
(in zone, not moving) -> a short anxious spell with whimpers -> back to relaxed.

- FrameEvents at ~8 fps with plausible keypoints that move (a dog-shaped stick figure is fine) and
  features that match the script, including occasional null features.
- AudioEvents during the vocal parts.
- RulesLabel updates consistent with the script.
- latest_frame_jpeg() returns a generated image (grey background, the stick figure drawn) so the
  video panel has something to show.
- mark_treat() jumps the script to the "excited" phase.

A config flag (pipeline: mock | real) picks MockPipeline or Person A's Pipeline. Everything else in
the backend must be unaware of which one is running.
```

---

## Step 3 — Backend core: FastAPI, WebSocket, event log  **[plan first]**

```
Read CLAUDE.md. Implement backend/main.py.

- On startup, construct the pipeline (mock or real, from config) and start run() as a background task.
  Never block the event loop; if the real pipeline uses threads, bridge callbacks safely into asyncio.
- WebSocket /ws broadcasting an envelope: {"type": "frame" | "audio" | "rules" | "emotion" | "llm" |
  "notification" | "treat" | "status", "data": {...}}. Throttle frame messages to what the dashboard
  needs (keypoints + features, no images) and drop, never queue, when a client is slow.
- GET /video: MJPEG stream from pipeline.latest_frame_jpeg() at the configured fps.
- POST /treat: calls pipeline.mark_treat(now), broadcasts a treat message, and records it.
- GET /events?since=: recent history from an in-memory ring buffer; also append every event to
  out/session-<timestamp>.jsonl.
- GET /health and GET /status (pipeline type, fps, provider/model, demo mode, last LLM latency).
- Graceful shutdown calls pipeline.stop().

Tests with pytest-asyncio against MockPipeline: WS receives each message type; /treat changes
the mock's phase; /video returns multipart JPEG.
```

---

## Step 4 — LLM interpreter (Groq / OpenRouter)  **[plan first]**

```
Read CLAUDE.md, "Fusion logic -> LLM interpreter". Follow it exactly.

Implement backend/fusion/llm_interpreter.py:
- One async client: the openai SDK with base_url and api_key from env. Provider, model and vision
  support come only from LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_VISION. No model names in code.
- OpenRouter: send the recommended attribution headers (HTTP-Referer, X-Title) from config.
- Trigger logic: treat event, AudioEvent score > 0.6, rules-label change, 10 s heartbeat; never more
  than one call per min interval; drop a trigger if a call is already in flight (latest wins).
- Request: system prompt + user content with the last ~3 s of features, recent AudioEvents, the current
  rules label, and (if LLM_VISION=1) the latest frame as a base64 JPEG image_url part, resized to
  ~512 px on the long side.
- System prompt: the dog is in a feeding area viewed by a static camera; answer ONLY with
  {"emotion", "confidence", "reason"}; emotion from the fixed vocabulary; the reason must cite
  observable cues only (posture, tail, ears, mouth, sounds), never guesses about the owner or history;
  say "unknown" when the dog isn't clearly visible. Keep the prompt in a separate prompts.py so it's
  easy to iterate on.
- Ask for JSON mode where supported, but parse defensively: strip fences, extract the first JSON
  object, validate against the vocabulary, clamp confidence. On timeout (8 s), error or bad output,
  return None and log it.
- Log provider, model, latency, tokens if available, and outcome for every call.

Add scripts/llm_smoke_test.py that sends one sample frame + features to the configured provider and
prints the parsed result and latency. I'll run it once with Groq and once with OpenRouter.
```

---

## Step 5 — Final label and state machine

```
Read CLAUDE.md, "Final label" and "Notifications".

Implement backend/fusion/state.py:
- Combine the latest RulesLabel and the latest valid LLM result into an EmotionState using the
  rules in CLAUDE.md (agree -> fused, mean confidence; disagree -> LLM only if confidence >= 0.7,
  else rules; no dog > 2 s -> unknown). Ignore LLM results older than a configurable staleness window.
- A state machine: a new emotion becomes the current state only after persisting for >= 3 s.
- Emit an EmotionState on every state change, plus a lightweight "emotion" broadcast at ~1 Hz for the
  live card.
- Decide notifications: on state change, respecting per-emotion cooldowns (default 60 s); fearful,
  aggressive and disinterested (beyond threshold) always notify, subject to cooldown.
- Keep a session timeline (list of state spans) for the dashboard and the demo.

Pure logic, no I/O, so it's fully unit-testable. Tests: flicker doesn't change state; the
disagreement rule works both ways; cooldown suppresses a repeat; stale LLM results are ignored.
```

---

## Step 6 — Telegram notifications

```
Read CLAUDE.md, "Notifications".

Implement backend/notify/telegram.py:
- Async sendPhoto to TELEGRAM_CHAT_ID with the latest frame as the photo and a caption:
  emoji + emotion, confidence, the one-line reason, and the time.
- Non-blocking: failures are logged and broadcast as a "notification" message with status "failed",
  never raised into the main loop. Retry once.
- A NOTIFY_MODE setting: telegram | dashboard_only. In dashboard_only (and in demo mode when offline)
  the notification appears as an in-app toast marked "would send to owner".
- scripts/telegram_test.py sends one test photo. Include short instructions in a docstring for
  creating a bot with BotFather and finding the chat id.
```

---

## Step 7 — Dashboard, part 1: layout and live state  **[plan first]**

```
Read CLAUDE.md, "Dashboard". Use frontend/lib/contracts.ts for all types.

Build the main page:
- A WebSocket hook with auto-reconnect and a connection indicator.
- Layout: large video panel on the left; on the right, the current emotion card (big label, emoji,
  confidence bar, reason text, source badge rules/llm/fused, "updated Xs ago"), below it a
  "Treat dropped" button that POSTs /treat and gives instant visual feedback.
- A status bar: pipeline type (mock/real), fps, LLM provider + model + last latency, demo mode.
- A notification toast stack.
- Works on a laptop screen and a projector; readable from a distance (large type for the emotion card).

Design: calm and clean, a colour per emotion used consistently everywhere (card, timeline, toasts).
Pick a palette that works in both light and dark mode.
```

---

## Step 8 — Dashboard, part 2: video overlay, timeline, audio strip

```
Read CLAUDE.md and the existing frontend.

- Video panel: show /video (MJPEG) and draw the dog's bbox, skeleton and face points on a canvas
  overlay from the latest frame message, scaled to the displayed size. Skip null keypoints. The
  overlay may lag the image slightly; that's acceptable, but keep it under ~200 ms.
- Emotion timeline: horizontal bar of state spans for the session, coloured per emotion, with treat
  markers and notification markers; hover shows the reason.
- Audio strip: small icons for bark / yip / growl / whimper / howl on the same time axis.
- Feature mini-panel (collapsible): live tail height, wag Hz, ear position, mouth open, body lowering,
  motion energy — useful for explaining the system to judges.
- A live/fallback toggle that calls a backend endpoint to switch mode (add POST /mode in main.py).
```

---

## Step 8b — Phone camera page and /ingest  **[new, plan first]**

```
Read CLAUDE.md, "Phone camera (primary live source)" and "The handoff interface".

1. Backend: WebSocket /ingest in main.py, exactly per CLAUDE.md. First message is the JSON hello;
   then binary messages where byte 0 is the kind: 0x01 + JPEG -> pipeline.ingest_frame(jpeg,
   time.time()); 0x02 + Int16 LE mono PCM -> pipeline.ingest_audio(pcm, hello.sample_rate,
   time.time()). One phone at a time: reject a second connection with a clear message. Never block
   the event loop; ingest calls are cheap by contract. Broadcast "status" messages to dashboard
   clients: phone connected/disconnected, received fps, pipeline.status().
2. Frontend: the /camera page, mobile-first, following the Claude Design screens:
   permission explainer -> camera picker (default: back camera) -> "point at the food bowl" setup
   with the feeding-zone guide -> streaming view (live dot, sent fps, connection quality, mic level
   meter, big Stop). Capture: draw the video onto a canvas at the configured fps, downscale to ~640 px
   on the long side, toBlob JPEG quality ~0.7, send with the 0x01 prefix, and skip a frame if the
   socket's bufferedAmount is high. Audio: an AudioWorklet capturing mono Float32 at the context's
   native rate, converted to Int16, sent as ~100 ms chunks with the 0x02 prefix. Screen Wake Lock
   where supported, auto-reconnect with backoff, works in portrait and landscape.
3. HTTPS: camera/mic only work over HTTPS. The page reads the backend wss:// URL from
   NEXT_PUBLIC_BACKEND_URL or a ?backend= query param. Add the two cloudflared quick-tunnel commands
   to the Makefile/npm scripts and to docs/DEMO_RUNBOOK.md.
4. MockPipeline: accept ingest_frame/ingest_audio/status too, and when phone frames arrive, use
   them for latest_frame_jpeg(), so the whole phone -> laptop path can be tested before Person A's
   pipeline is ready.
5. Dashboard: show a clear "Phone camera disconnected" state when status says stalled.

Test on a real phone (iPhone Safari and Android Chrome if we have both): stream for 5 minutes, see
the phone's video on the laptop dashboard, then kill the page and reopen it; the dashboard must
recover without restarting anything.
```

---

## Step 9 — Demo mode  **[plan first]**

```
Read CLAUDE.md ("Fallback footage", "Non-negotiables") and data/fallback/manifest.json once Person A
has created it (until then, use a placeholder manifest with one mock clip).

Demo mode must work with ZERO network. Design:
1. scripts/precompute_demo.py: for each clip in the manifest, read Person A's events/<clip>.jsonl,
   replay it through state.py and the LLM interpreter in accelerated time (extracting the frame at
   each LLM trigger from the clip with OpenCV), and write backend/demo/cache/<clip>.timeline.json:
   every EmotionState, LLM result, notification and treat marker with its clip-relative timestamp.
   Run this while online; the output is committed.
2. In demo mode the frontend plays the clip itself in a <video> element (served from the backend)
   and renders frame/audio/emotion/notification events from events.jsonl + timeline.json keyed to
   video.currentTime, so everything stays in sync even if you pause or seek.
3. Notifications in demo mode are dashboard toasts; if the network happens to be up and a flag is set,
   also send the real Telegram message for the first notification only.
4. A clip picker, and a one-key shortcut to jump between live and demo.

Test: switch the laptop to airplane mode and run the full demo.
```

---

## Step 10 — Integrate Person A's real pipeline

```
Read CLAUDE.md and docs/DATA_HANDOFF.md if Person A has written it.

Switch config to pipeline: real and run against a fallback clip in file mode, then the webcam.
Check: callbacks arrive at the expected rates; events validate against contracts.py; the overlay lines
up with the video; mark_treat reaches the rules engine; nothing blocks the event loop (measure WS
latency under load). Report every mismatch and say whose side it's on before fixing anything. Only
fix issues in my own files; for Person A's side, write the bug report I should send them.
```

---

## Step 11 — Provider comparison and hardening

```
Read CLAUDE.md.

1. Run scripts/llm_smoke_test.py-style benchmarks over 20 saved frames with Groq and with OpenRouter
   (each with the vision model we're considering). Report median/p95 latency, parse-failure rate and
   whether labels agree with the rules label. Recommend a default for the demo; I'll decide.
2. Failure drills: kill the network mid-session, revoke the API key, stop the pipeline, disconnect
   the websocket. The dashboard must show a clear status and keep running on rules-only labels.
3. Soak test: 20 minutes live on MockPipeline; check memory stays flat and the WS doesn't leak.
4. Update CLAUDE.md "Commands" with the real web-side commands and write docs/DEMO_RUNBOOK.md:
   start-up order, env checklist, how to switch to fallback, what to say if something fails.
```

---

## Handy one-liners for when things go wrong

```
Something in <file> is failing: <paste error>. Find the root cause, explain it in two sentences,
then fix it with the smallest change. Don't refactor anything else.
```

```
The dashboard feels laggy. Measure time from pipeline callback to WS send to render, and tell me
where the delay is before changing anything.
```

```
The LLM keeps returning <bad output>. Show me the exact request and raw response, then propose a
prompt change in prompts.py only.
```

```
Person A's events look wrong: <symptom>. Check the raw events against contracts.py and CLAUDE.md and
tell me whose side the bug is on.
```
