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
- **No native mobile app.** Notifications go through a Telegram bot (web push only if time allows).
- Feature freeze: early afternoon of day 2. After that, only bug fixes and rehearsal.

## Architecture

```
            ┌────────────── video source (webcam | stream | file) ──────────────┐
            ▼                                                                    │
  dog detection (YOLO, COCO "dog") ─► body keypoints (pretrained quadruped pose) │
            │                                  │                                 │
            └─► face crop ─► face landmarks ───┤                                 │
                                               ▼                                 │
                                     feature extraction ─► FrameEvent            │
                                                                                 │
  audio source (mic | file) ─► YAMNet ─► AudioEvent                              │
                                                                                 │
  FrameEvent + AudioEvent ─► fusion (rules + LLM interpreter) ─► EmotionState ─► dashboard
                                                                        └─► notifier (Telegram)
```

## Tech stack (draft: confirm before building)

- **Backend:** Python 3.11, FastAPI, WebSocket push to the dashboard
- **Vision:** Ultralytics YOLO (detection), DeepLabCut SuperAnimal-Quadruped or MMPose AP-10K
  (body keypoints), DogFLW-based landmark model (face)
- **Audio:** YAMNet via TensorFlow Hub, stock AudioSet classes
- **Interpreter:** provider-agnostic LLM layer over an OpenAI-compatible API (Groq or OpenRouter,
  chosen by config). No provider or model is hard-coded (see "LLM interpreter" below)
- **Frontend:** Next.js + React dashboard
- **Notifications:** Telegram Bot API

## Repo layout

```
/backend
  main.py              FastAPI app, WebSocket endpoint, event log
  sources.py           input abstraction: webcam | stream | file (one config flag)
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
  /notify
    telegram.py
  /demo
    cache/             pre-computed LLM responses for fallback clips
/frontend              Next.js dashboard
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

### AudioEvent (audio → fusion), one per YAMNet window (~0.96 s)
```json
{ "ts": 1727340000.5, "label": "bark", "score": 0.82 }
```
`label` is `bark | yip | growl | whimper | howl | silence | other`.

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

## Commands (fill in once scaffolded)
```
# backend
uvicorn backend.main:app --reload
# frontend
cd frontend && npm run dev
# demo mode (offline, fallback clips)
DEMO_MODE=1 uvicorn backend.main:app
```

## Environment variables
`LLM_PROVIDER` (`groq` | `openrouter`), `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`,
`LLM_VISION` (`1` | `0`), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DEMO_MODE`

## Team and ownership
Team size: TBD. Workstreams (collapse or expand to fit headcount):
1. Vision: detection, pose, face, features
2. Audio: YAMNet events
3. Backend: fusion, LLM interpreter, state, event log
4. Frontend + notifications
5. (if 5+) Fallback footage, demo script, pitch

## Two-day plan
**Day 1:** agree on contracts → source abstraction → pretrained pose on a clip → YAMNet events →
dashboard on mock data → Telegram test message → features + rules v1 → LLM prompt + provider switch →
one fallback clip end to end.
**Day 2:** tune rules on fallback clips → state machine + cooldowns → dashboard timeline →
cache LLM responses for demo mode → feature freeze → live test in venue → rehearse the fallback switch twice.

## Open questions
- Team size and owners per workstream
- Camera hardware and placement for the live demo
- How treat events are detected (recommended: a manual "treat dropped" button on the dashboard)
- Default provider and model for the demo (Groq vs. OpenRouter), decided from day-2 latency logs
