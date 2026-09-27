# Data → Web handoff

Everything Web needs to build against the `Pipeline` without reading `backend/vision/`, `backend/audio/`
or `backend/fusion/rules.py`. The JSON contracts (`FrameEvent`, `AudioEvent`, `RulesLabel`) are defined in
`backend/contracts.py` and mirrored in CLAUDE.md; this doc covers everything around them: how to run the
`Pipeline`, what its states mean, and where it's known to be weak.

## Constructing and running it

```python
import asyncio, time, yaml
from backend.pipeline import Pipeline

cfg = yaml.safe_load(open("config.yaml").read())
pipeline = Pipeline(cfg)   # loads every model; do this once, at startup, not per-request

def on_frame_event(ev): ...     # backend.contracts.FrameEvent
def on_audio_event(ev): ...     # backend.contracts.AudioEvent
def on_rules_label(label): ...  # {"ts", "emotion", "confidence", "scores": {emotion: 0..1}}

task = asyncio.create_task(pipeline.run(on_frame_event, on_audio_event, on_rules_label))
# ... serve requests; call pipeline.status(), pipeline.latest_frame_jpeg(), pipeline.mark_treat(ts),
#     pipeline.ingest_frame(jpeg, ts), pipeline.ingest_audio(pcm16, sample_rate, ts) any time ...
pipeline.stop()
await task
```

- **One `Pipeline` per process**, constructed once. Loading models takes a few seconds (DeepLabCut and
  the face TFLite model warm up their first inference call in `__init__`), so build it at startup, not
  lazily on the first request.
- **`run()` can only be called once** per `Pipeline`. It returns when the source ends (file, no loop) or
  `stop()` is called. It re-raises whatever killed the video thread (e.g. a webcam that disappears,
  a stream URL that stops resolving) — wrap the `await` in a try/except if you want to restart on that.
- **Callbacks always run on the event loop you called `run()` from**, never on a worker thread — safe to
  touch asyncio state (a WebSocket send, a queue) directly inside them. They must return quickly: the
  video loop never waits on them, and if they fall behind, the *oldest* buffered events are silently
  dropped (bounded to ~512, about a minute at 8 fps) rather than piling up unbounded. A callback that
  raises is logged (rate-limited to once per 5 s) and does not stop the pipeline.
- **`config` is the whole parsed `config.yaml`** (the full dict, not just `data`). Web's own sections
  (`llm`, `notify`, `demo_mode`, cooldowns) live alongside `data` in the same file; `Pipeline` only reads
  `data`.

## `ingest_frame` / `ingest_audio` / `mark_treat`

- **Only meaningful when `data.source.type` is `browser`.** On any other source type they are a no-op
  that logs one warning total (not one per call) and never raises — safe to wire up unconditionally from
  `/ingest` regardless of the configured source.
- **`ingest_frame(jpeg: bytes, ts: float)`**: one JPEG frame, `ts = time.time()` at receipt on your server
  (not a client timestamp). Decoding happens on the video thread; frames pushed faster than `data.fps`
  are dropped undecoded, and the pipeline always processes the *newest* pushed frame, never a queue of
  stale ones.
- **`ingest_audio(pcm16: bytes, sample_rate: int, ts: float)`**: mono Int16 little-endian PCM, `ts` = time
  at receipt for the *end* of that chunk. Internally buffered up to 5 s; older audio is dropped first if
  you get behind. Resampled to 16 kHz automatically; you don't need to resample before calling this.
- **Both are non-blocking and never raise** — safe to call directly from your WebSocket receive loop. A
  malformed chunk (wrong type, odd byte length) is dropped with a rate-limited log line, not an exception.
- **`mark_treat(ts: float)`**: call this when the "treat dropped" button is pressed, with `time.time()`.
  Sets `treat_event_recent` for the rules engine for `data.rules.treat_window_s` (default 10 s) starting
  from `ts`. It's a fire-and-forget flag, not a queue — pressing it again while already active just
  extends the window from the new `ts`.

## `status()`

```json
{"source": "browser", "state": "running", "fps": 7.8, "last_frame_age_s": 0.12, "audio_ok": true}
```

| Field | Meaning |
|---|---|
| `source` | The configured `data.source.type`: `browser \| webcam \| stream \| file`. |
| `state` | `"running"`, `"stalled"`, or `"stopped"`. See below. |
| `fps` | Frames actually processed per second, measured over the last ~3 s of real time. `0.0` if nothing has landed recently — don't divide by it. |
| `last_frame_age_s` | Seconds since the last frame arrived. `null` before the first frame (or always `null` for `file`/non-live sources, where "age" isn't meaningful). |
| `audio_ok` | `true` if an audio chunk has arrived within `data.source.stall_s` (browser) or audio is otherwise flowing. Independent of `state` — video can be `running` while `audio_ok` is `false` (dead mic) and vice versa. |

**`state` values:**
- **`"running"`**: normal.
- **`"stalled"`**: a *live* source (`browser`/`webcam`/`stream`) has gone > `data.source.stall_s` (default
  2 s) without a frame — phone backgrounded, wifi dropped, camera unplugged. The pipeline does **not**
  stop or need a restart: a watchdog thread keeps feeding "no dog" frames so `on_rules_label` receives
  `unknown` (see `no_dog_unknown_s` in the rules config) and the dashboard can show a "no signal" state.
  It clears back to `"running"` automatically the moment frames resume — nothing to call on reconnect.
  `file` sources never report `"stalled"` (there's no "live connection" to lose).
- **`"stopped"`**: `stop()` has been called, or the source ended and was never running. Calling any method
  after this is safe (all become no-ops) except `run()` again, which raises.

## Callback frequencies

- **`on_frame_event`**: once per processed frame, `~5–10/s` (`data.fps`, default 8) for live/real-time
  sources. In `--fast` file mode (batch/offline), as fast as the machine can go, not paced to real time.
  Includes frames where `dog_detected` is `false` — don't filter those out before checking the flag.
- **`on_audio_event`**: **not** a steady rate. One YAMNet window is analysed every `data.audio.hop_s`
  (~0.48 s), but events are only emitted on an *onset or change* — the same dog label repeats at most once
  per `data.audio.debounce_s` (default 1 s), and `silence`/`other` only fire when they differ from the
  previous window. Long stretches with nothing new mean no events at all; treat "the current sound" as
  the latest event you received, not something to expect every N ms.
- **`on_rules_label`**: once per processed frame, i.e. the same rate as `on_frame_event` — always exactly
  one `RulesLabel` per `FrameEvent`, in the same order, after hysteresis is applied. Its `.scores` dict
  always has all 8 emotion keys (`happy`, `excited`, `relaxed`, `anxious`, `fearful`, `aggressive`,
  `disinterested`, `unknown`), even ones far from being emitted.

## What `null` means, per feature

Every `Features` field is independently nullable — a `FrameEvent` with `dog_detected: true` can still have
every feature `null` if the pose/face models found nothing usable. Never treat a missing feature as "0" or
"false"; the rules engine already excludes null evidence from its scoring rather than penalizing it, and
Web's LLM interpreter/dashboard should do the same (e.g. don't draw a tail-height bar at 0 when it's null —
that's a real, low value, different from "unknown").

| Feature | Null when | Non-null range |
|---|---|---|
| `tail_height` | Tail base/tip keypoints missing or low-confidence, or motion window too short | −1 (tucked) .. 1 (high) |
| `tail_wag_hz` | Same as above, or not enough history yet to estimate a frequency | ≥ 0 (Hz; 0 = still) |
| `ear_position` | No usable ear/eye/nose geometry (from face landmarks or body keypoints) | `"up" \| "neutral" \| "back"` — note **`"unknown"` is a distinct valid value**, not the same as `null`; it means "we tried and couldn't tell", `null` here specifically means no scale/reference to even attempt it |
| `mouth_open` | No usable mouth landmarks | 0 (closed) .. 1 (wide open) |
| `body_lowering` | No usable withers/height reference, or no baseline yet (`baseline_s`, default 30 s of history) | 0 (standing tall) .. 1 (fully lowered) |
| `motion_energy` | Fewer than 2 confident keypoints in the recent window (this is the feature most sensitive to a partial pose failure — see "Known weaknesses") | 0 (still) .. 1 (fast) |
| `in_feeding_zone` | Only when `dog_detected` is `false` | `true`/`false` — this one comes from the **detection bbox**, not pose, so it survives a total pose failure |

`bbox`/`bbox_conf`/`dog_detected` never depend on pose or face — they come straight from the detector, so
they're the most reliable fields in a `FrameEvent` and the right thing to check first for "is there even a
dog in frame".

## Where the fallback pack lives

```
data/fallback/
  clips/<name>.mp4          H.264 + AAC, 30 fps — what demo mode plays
  events/<name>.jsonl       one envelope per line: {"type": "frame"|"audio"|"rules"|"treat", "data": {...}}
  manifest.json             per-clip duration, treat timestamps, expected_emotion, observed dominant label
```

`events/<name>.jsonl` timestamps are **seconds from clip start**, not wall time — add your own replay
start time when feeding them through demo mode. `manifest.json`'s `treats` are in the same seconds and
line up with a `"type": "treat"` line at that timestamp in the events file. Full schema and how to
regenerate this pack: see "Fallback pack" in CLAUDE.md and `scripts/precompute_events.py`'s docstring.

Two clips in the manifest are flagged in their `notes` as **known rules-only misses** (`idle_labrador`,
`vocal_chained`) — the rules label won't match `expected_emotion` for reasons explained there (short
clip / duration-rule timing, and a bark-vs-whimper audio-bonus gap respectively). Both are plausible
candidates for the LLM interpreter to get right by reading the frame directly; don't treat them as pack
bugs to fix.

## Known weaknesses

- **A partial pose failure degrades to null features, not a dropped frame.** If the pose or face model
  raises (bad weights, device issue) or simply finds nothing, you still get a `FrameEvent` with
  `dog_detected`/`bbox`/`in_feeding_zone` intact and every pose-derived feature `null` (ear_position
  specifically becomes `"unknown"`, not `null` — see the table above). Verified in
  `tests/data/test_pipeline.py::test_pose_or_face_model_failing_still_emits_detection_only_frame_events`.
- **A dead mic/audio path drops audio silently, video is unaffected.** `status()["audio_ok"]` goes
  `false`; no exception, no `on_audio_event` calls, `on_frame_event`/`on_rules_label` keep flowing.
  Verified in `test_mic_or_audio_pipeline_failing_drops_audio_but_not_video`.
- **A stalled/disconnected live source needs no restart** — see `state` above. Verified in
  `test_a_stalled_phone_drops_the_rules_to_unknown_and_recovers` and
  `test_never_connected_browser_is_stalled_after_the_grace_period`.
- **`motion_energy` is the feature most likely to go null**, because it needs at least 2 confident
  keypoints across consecutive frames. A dog lying mostly still and slightly occluded (see
  `idle_labrador` in the fallback pack) can go several seconds with `motion_energy: null`, which starves
  the `disinterested` duration rule of the evidence it needs — this is a real limitation, not a bug, and
  is exactly the kind of gap the LLM interpreter's raw-frame view is meant to cover.
- **Sustained fps is below the configured `data.fps` (8) whenever a dog is actually in frame**, because
  detect + pose + face run sequentially per frame on one thread. Measured on the M2 over two clean 5-minute
  runs (`scripts/run_pipeline.py`, `data.fps: 8`):

  | Mode | Mean fps | Stage cost (mean ms/frame) | CPU |
  |---|---|---|---|
  | Browser, real dog clip on loop (`eating_home.mp4`) | **4.7** (range 2.5–6.2; 59% of 5 s windows below 5 fps) | detect 45, pose 92, face 92, total 232 | ~200% (2 cores) |
  | Webcam, empty room (no dog ever detected) | 8.0 (essentially the configured target) | detect 43, pose 0, face 0 | ~32% |

  The webcam number is **not** a "webcam is fine" result — pose and face never ran because no dog was ever
  in frame for the full 5 minutes, so it only measures YOLO detection cost. The browser number, replaying
  an actual dog on loop, is the representative one for the demo (a feeding-zone camera has a dog in frame
  most of the time) and is the one that matters: **it sits below the 5 fps bar**, with pose and face
  contributing roughly equally (~40% of frame time each) and detect a smaller ~19% share. A live/real-time
  source drops frames to the newest one rather than falling behind, so this shows up as `status()["fps"]`
  settling around 4–5 rather than the callback queue growing unbounded — the dashboard won't lag or crash,
  it'll just update at ~5 fps instead of 8 when a dog is present.

  **Tried and reverted:** swapping `data.pose.model` to DeepLabCut's `resnet_50` variant (same SuperAnimal-
  Quadruped zoo) cut pose from ~92ms to ~37ms and raised mean fps to 7.5 — a clean win on paper, and its
  keypoint *coverage* was as good or better than `hrnet_w32`'s on the same clip. But its keypoint *values*
  differ enough that two of the five fallback clips that matched their `expected_emotion` after Step 12's
  tuning (`eating_home`, `treat_beach`) flipped to a wrong label (`disinterested`, `excited`) once every
  clip was re-run against it — the rules thresholds were tuned against `hrnet_w32`'s specific noise profile,
  and swapping the pose model silently invalidates that tuning. Reverted to `hrnet_w32`; **the sub-5fps
  finding stands, unfixed, for now.**
  - **Still open, not implemented:** run pose (and face, since it depends on pose's keypoints) on every
    other frame and interpolate the tail/ear/mouth/motion features between real detections. Should roughly
    halve the pose+face share of frame time without touching keypoint *values* on the frames where they do
    run, so it's much less likely to disturb the tuned rules thresholds than a model swap — but it's a real
    code change (`Pipeline._process` / `FeatureExtractor`), not a config flip, and untested. Worth trying if
    there's time before the freeze; re-run `scripts/precompute_events.py` + `scripts/tune.py` afterward and
    compare against the current match table (see the reproducibility note just below first).
  - Lowering YOLO's `detect.imgsz` (640→480) was estimated, not tested live end-to-end after the revert;
    it only touches `detect`'s ~45ms share (~19% of frame time) so it wouldn't clear 5 fps alone, but
    combined with the interpolation change above it's a reasonable second lever.
- **Model inference on MPS isn't bit-for-bit reproducible run to run, even in `--fast` mode.** `--fast`'s
  *control flow* is deterministic (frame order, timing, which events fire in which order), but YOLO/
  DeepLabCut's floating-point results on Apple's MPS backend vary slightly between runs. Confirmed directly:
  two back-to-back `--fast` runs of `treat_poodle.mp4` (identical config, identical clip) gave `happy`
  dominant one time and `excited` the next. This only matters when a rules decision is a near-tie — 
  `treat_poodle`'s `excited` vs `happy` scores sit at ~48–52% either way, so it's the one fallback clip
  that can flip between `precompute_events.py` runs; the other six were stable across every re-run this
  session. **Don't treat the fallback pack's match table as a fixed number** — re-running the pack can
  shift `treat_poodle` by one label without anything actually being broken. If a stable regression suite
  matters later, pin `torch`/DeepLabCut to CPU (`data.pose.device: cpu`) for a deterministic (but slower)
  comparison baseline.
- **`ear_position` is the noisiest feature even when non-null** — it's inferred from a 2-point vector
  (ear base → tip) against the head axis, no dedicated model. Treat it as a weak signal.
- **Body proportions aren't breed-normalized.** `body_lowering`'s baseline is this dog's own recent
  standing height (`baseline_s`, 30 s), so short-legged breeds (corgi) don't get a systematically wrong
  reading, but a breed change or camera angle change mid-session takes ~30 s to re-baseline.

## Running it yourself

```
python scripts/run_pipeline.py --source file --path data/fallback/clips/eating_home.mp4 --fast \
    --jsonl out/events.jsonl --debug-video out/debug.mp4 --treat-at 6
python scripts/run_pipeline.py --source browser --path some_clip.mp4 --portrait   # simulates a phone
python scripts/run_pipeline.py --source webcam --duration 30
```

`--fast` (file source only) is the deterministic offline batch mode used to build the fallback pack:
nothing is paced to real time or dropped, and stage timings don't depend on machine speed. Live/webcam/
stream/browser modes are always real-time and can drop frames.
