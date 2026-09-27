# Behind the Barks

A web app that watches a dog through a single static camera (plus a microphone) pointed at a
feeding area, infers an emotional state — happy, anxious, excited, and so on — shows it on a
live dashboard, and pushes a notification to the owner when that state meaningfully changes.

Built in a 2-day hackathon window as a two-person project: one person owns the vision/audio
pipeline that turns a video+audio stream into structured signals, the other owns the web
backend, LLM interpretation layer, and dashboard that turns those signals into something an
owner actually looks at.

## Description

Most "smart pet camera" products either livestream video with no interpretation, or bolt on a
generic motion alert. Behind the Barks instead runs an actual perception pipeline — dog
detection, body pose, facial landmarks, bark/whine classification — through a rules engine and
an optional LLM interpreter, and reduces all of that down to one of a fixed, small vocabulary of
emotional states with a plain-English reason ("high, fast tail wag and open mouth as the treat
drops; two short yips"). It's built around a single constrained scenario (a dog at its feeding
bowl) rather than trying to generalize to every room and every dog, which is what makes a 2-day
build achievable and what keeps the rules tunable against a handful of reference clips.

Design choices that shape the project:

- **No models trained from scratch.** Detection, pose and audio classification are pretrained
  (Ultralytics YOLO, DeepLabCut SuperAnimal-Quadruped, YAMNet); only an optional facial-landmark
  fine-tune is in scope.
- **The offline fallback is not an afterthought.** Every live dependency (LLM API, Telegram) has
  a cached or stubbed path, so the demo can run in airplane mode end to end.
- **The LLM layer is provider-agnostic.** One OpenAI-compatible client, swapped between Groq and
  OpenRouter by config, never hard-coded.
- **A phone's browser is the live camera.** No native app; `getUserMedia` in a web page streams
  frames and audio to the backend over a WebSocket.

See [`CLAUDE.md`](CLAUDE.md) for the full architecture, JSON contracts between the vision/audio
side and the web side, the emotion vocabulary, and the fusion/rules logic in detail.

## Visuals

Static mockups of every screen (desktop and mobile dashboard, camera setup and streaming states,
notifications, system states, design tokens) live under [`ui/screens/`](ui/screens/) as
standalone HTML files you can open directly in a browser. There's no hosted demo; run it locally
(below) to see the live dashboard and camera page.

## Installation

The two sides have separate dependency sets so that building the dashboard never requires the
heavy ML stack, and vice versa.

**Prerequisites:** Python 3.11 or 3.12 (not yet 3.13/3.14 — TensorFlow has no wheels for those at
time of writing), Node.js 18+, and `cloudflared` if you want the live phone-camera path
(`brew install cloudflared`).

```bash
# Data side (vision/audio pipeline) — heavier install, ~5-10 min
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-data.txt
python scripts/check_env.py            # verifies torch/MPS, tensorflow, ultralytics actually import
python scripts/fetch_face_model.py     # downloads the DogFLW face-landmark model once (needs network)

# Web side (backend + dashboard)
pip install -r requirements-web.txt    # can go in the same venv, or a separate one — no dependency overlap
cd frontend && npm install && cd ..
```

Copy `.env.example` to `.env` and fill in what you have (LLM provider/key, Telegram token) — every
field is optional; anything left blank degrades gracefully (rules-only labels, dashboard-only
notifications).

## Usage

**Fastest path — no camera, no API keys, fully offline:**

```bash
DEMO_MODE=1 make dev-backend    # or: DEMO_MODE=1 .venv/bin/python -m uvicorn backend.main:app --port 8000
cd frontend && npm run dev      # http://localhost:3000
```

Open the dashboard, flip to **Demo**, and pick one of the pre-recorded fallback clips — video,
inferred emotions, notifications and the timeline all replay from cached data with zero network
calls.

**Development, against the mock pipeline** (`config.yaml`'s default — no ML deps needed):

```bash
make dev          # backend (mock pipeline) + frontend together
make test-web     # pytest tests/web
make test-frontend
```

**Against the real vision/audio pipeline, on a video file:**

```bash
python scripts/run_pipeline.py --source file --path data/fallback/clips/eating_home.mp4 --fast \
    --jsonl out/events.jsonl
pytest tests/data
```

**Live, with your phone as the camera:** set `data.source.type: browser` and `web.pipeline: real`
in `config.yaml`, then:

```bash
make dev-backend                     # real pipeline, waits for a phone stream
cd frontend && npm run dev
make tunnel-backend                  # cloudflared -> https://<X>.trycloudflare.com
make tunnel-frontend                 # cloudflared -> https://<Y>.trycloudflare.com
```

On the phone, open `https://<Y>.trycloudflare.com/camera?backend=https://<X>.trycloudflare.com`,
allow camera + mic, and point it at the feeding area. Full walkthrough, troubleshooting and
failure drills are in [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md).

Don't leave `config.yaml` on `source: browser` / `pipeline: real` as your working default —
`DEMO_MODE` doesn't override `web.pipeline`, so the offline fallback demo would try to open the
real pipeline instead of replaying cached clips.

## Support

This is a hackathon-scale, two-person project rather than a maintained product, so there's no
issue tracker or support channel to point to. If you're picking this repo up:

- [`CLAUDE.md`](CLAUDE.md) is the source of truth for architecture, JSON contracts, and rules
- [`docs/DEMO_RUNBOOK.md`](docs/DEMO_RUNBOOK.md) covers startup order, env vars, and failure drills
- [`docs/DATA_HANDOFF.md`](docs/DATA_HANDOFF.md) and [`docs/DATA_HANDOFF_WEB.md`](docs/DATA_HANDOFF_WEB.md)
  document what each side handed to the other and why specific tuning choices were made

## Roadmap

The build plan was a strict two-day window with a feature freeze at day-2 early afternoon (see
`CLAUDE.md`'s two-day plan); that freeze has passed and the project is in hardening/handoff mode.
Open items, in the order they'd matter next:

- Decide a default LLM provider/model for the demo from day-2 latency logs (Groq vs. OpenRouter)
- Web push notifications, if there's time, alongside the existing Telegram path
- Optional DogFLW fine-tune of the face-landmark model, only if the pretrained one turns out to
  need it on a wider range of dogs
- Anything past that is explicitly out of scope for this build (no native mobile app, no
  training detection/pose models from scratch)

## Contributing

This was built by exactly two people against one rule: **Data produces events, Web consumes
them.** Everything left of the JSON contracts in `CLAUDE.md` (vision, audio, rules) belongs to
Data; everything right of them (backend, LLM interpreter, dashboard, notifications) belongs to
Web. Neither side edits the other's folders without agreeing on it first, and a JSON contract
only changes alongside an update to `CLAUDE.md` in the same commit.

If you're extending this:

1. Read `CLAUDE.md` in full before touching either side — it defines the `Pipeline` interface,
   the `FrameEvent`/`AudioEvent`/`EmotionState` contracts, and the emotion vocabulary, none of
   which are meant to drift silently.
2. Run the relevant test suite before and after: `pytest tests/data`, `pytest tests/web`,
   `cd frontend && npm run test && npm run typecheck && npm run lint`.
3. If you touch `requirements-data.txt`, re-resolve it in a clean venv
   (`pip install --dry-run -r requirements-data.txt`) before committing — `ultralytics` and
   `deeplabcut` pins have conflicted with each other before (see the pins' inline comments).

## Authors and acknowledgment

Built by a two-person team (Data: vision/audio pipeline and rules; Web: backend, LLM interpreter,
dashboard and notifications) over a two-day build window.

Built on pretrained work from others, without which this wouldn't have been a 2-day project:
Ultralytics YOLO, DeepLabCut's SuperAnimal-Quadruped model, `hugocornellier/dog-face-landmarks`
(DogFLW-based facial landmarks, CC BY-NC 4.0, non-commercial), and Google's YAMNet /
AudioSet. Fallback clips and audio are sourced from Pexels, Pixabay, ESC-50 and AudioSet, with
licence and source recorded per file in [`data/fallback/SOURCES.md`](data/fallback/SOURCES.md).

## License

No license has been chosen for this project yet — until one is added, all rights are reserved by
default and the code shouldn't be reused. Note also that the bundled face-landmark model weights
(`hugocornellier/dog-face-landmarks`) are CC BY-NC 4.0 (non-commercial) regardless of whatever
license this repo eventually adopts.

## Project status

Feature-complete for the 2-day build's scope; past the feature freeze and in hardening/handoff
mode (see [`docs/DATA_HANDOFF.md`](docs/DATA_HANDOFF.md)). Both sides' test suites pass, the
offline fallback demo works in airplane mode, and the live phone-camera path has been verified
end to end.
