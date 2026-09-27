# Claude Code prompts — Person A (Data)

Run these **in order**, one prompt per step. Commit after each step passes its checks, then `/clear`
before the next prompt so every step starts from CLAUDE.md and the repo rather than a stale context.
For prompts marked **[plan first]**, switch to plan mode (Shift+Tab) and approve the plan before any
code gets written.

> **Revision (after Step 8):** the live camera is now a phone browser. Steps 0–8 are unchanged. A new
> **Step 9 (phone source)** has been inserted; the old Steps 9–12 are now **10–13**, and Steps 10 and
> 13 cover the new Pipeline methods (`mark_treat`, `ingest_frame`, `ingest_audio`, `status`). Pull the
> updated CLAUDE.md into the repo before running Step 9.

Every prompt assumes CLAUDE.md is at the repo root. Keep it current, because it is the shared source
of truth between you and Person B.

---

## Step 0 — Bootstrap the data side  **[plan first]**

```
Read CLAUDE.md fully. I am Person A (Data). I own backend/sources.py, backend/vision/,
backend/audio/, backend/fusion/rules.py and data/. Do not create or edit anything that belongs to
Person B (backend/main.py, llm_interpreter.py, state.py, notify/, demo/, frontend/).

Set up the data side on my M2 MacBook (Apple Silicon, no CUDA):
1. Python 3.11 virtual env and a requirements-data.txt. Pin versions. Verify each heavy dependency
   (torch with MPS, tensorflow + tensorflow-hub, ultralytics, opencv-python, sounddevice, numpy,
   scipy, pydantic) actually installs and imports on macOS arm64. If one doesn't, tell me and propose
   an alternative before continuing.
2. Scaffold the folders from the "Repo layout" section of CLAUDE.md for my areas only, with empty
   modules and docstrings.
3. Add a data section to config.yaml (create the file if Person B hasn't): source type/path/device,
   target fps (default 8), feeding-zone polygon in normalised coords, keypoint confidence threshold
   0.3, rules thresholds as a nested block. If config.yaml already exists, only add my section.
4. Add pytest and a tests/data/ folder.
5. Write a short scripts/check_env.py that prints torch MPS availability, TF version, and whether a
   webcam and microphone can be opened.

Done when: `python scripts/check_env.py` runs clean and `pytest` passes with zero tests.
```

---

## Step 1 — Contracts and the Pipeline interface

```
Read CLAUDE.md, especially "JSON contracts" and "The handoff interface".

Create backend/contracts.py with pydantic v2 models: FrameEvent (with nested Features),
AudioEvent, RulesLabel ({ts, emotion, confidence, scores}), and the Emotion literal from the fixed
vocabulary. Field names, types, ranges and nullability must match CLAUDE.md exactly. Add validators
for the ranges (tail_height -1..1, others 0..1) and a to_json() helper.

Then create backend/pipeline.py with the Pipeline class signature from CLAUDE.md, fully typed, with
NotImplementedError bodies and docstrings explaining what each callback receives and how often.

This file is shared with Person B, so keep it minimal and obvious. If backend/contracts.py already
exists, diff it against CLAUDE.md and report mismatches instead of overwriting.

Tests: round-trip each model through JSON; reject out-of-range values; accept null features.
```

---

## Step 2 — Input sources

```
Read CLAUDE.md. Implement backend/sources.py.

One interface, three video backends chosen by config: webcam (device index), stream (URL, e.g. a
phone camera app), file (video path). Plus audio: microphone (sounddevice) or file. For file mode,
extract the audio track with ffmpeg if present, and allow an explicit audio override path because
fallback clips may get audio from a separate file.

Requirements:
- Yield (timestamp, BGR frame) and (timestamp, mono float32 16 kHz chunk) with timestamps on the same
  clock so video and audio events line up.
- File mode plays back at real-time speed by default, with a --fast flag for batch processing.
- Loop option for files (for the demo).
- Downsample video to the configured fps; never buffer unboundedly (drop old frames, keep newest).
- Clean shutdown, no hanging threads.

Add a CLI: python -m backend.sources --source file --path X shows the frames in a window and prints
audio RMS once a second. Test with any short mp4.
```

---

## Step 3 — Dog detection and feeding zone

```
Read CLAUDE.md. Implement backend/vision/detect.py.

Use Ultralytics YOLO (small model, pretrained on COCO) and keep only class "dog". Run on MPS if
available, else CPU. Return the single best dog per frame (highest confidence, tie-break by largest
area), its bbox and confidence, or None.

Also:
- in_feeding_zone: whether the dog's bbox centre (or bottom-centre, pick and justify) lies inside the
  feeding-zone polygon from config.
- A padded crop helper for the pose stage.
- Simple temporal smoothing of the bbox (EMA) to stop jitter.
- Report per-frame latency. Target: under 40 ms on the M2 for the small model.

Test on a stock dog clip and write an annotated debug video to out/debug_detect.mp4 with the
bbox and zone polygon drawn.
```

---

## Step 4 — Body pose  **[plan first]**

```
Read CLAUDE.md. Implement backend/vision/pose.py and backend/vision/keypoint_map.py.

First, research and compare options that run on Apple Silicon WITHOUT CUDA and need no training:
(a) DeepLabCut SuperAnimal-Quadruped (pretrained, PyTorch backend),
(b) MMPose AP-10K animal pose models,
(c) anything else pretrained for dogs/quadrupeds you can verify.
For each: install pain on macOS arm64, speed on a dog crop, keypoints available (we need at least
tail base, tail tip or tail mid, withers/back, hips, paws, nose, eyes, ear bases, ear tips if any).
Show me the comparison and recommend one before implementing.

Fallback if none work acceptably: Ultralytics' dog-pose dataset (Stanford Dogs based, 24 keypoints)
with a YOLO pose model fine-tuned on Colab. Write the Colab notebook but don't run it unless I ask.

Then implement:
- pose.py: an adapter class with estimate(frame, bbox) -> dict of raw keypoints (x, y, conf) in
  full-frame pixel coords.
- keypoint_map.py: map the chosen model's names to our canonical names (tail_base, tail_tip,
  withers, hip, nose, left_eye, right_eye, left_ear_base, left_ear_tip, right_ear_base,
  right_ear_tip, front paws, back paws, chin/jaw if available). Missing or below-threshold points
  become None, never guesses.
- Debug video out/debug_pose.mp4 with the skeleton drawn.

Report fps on the M2 for detection + pose combined. Target at least 5 fps.
```

---

## Step 5 — Face landmarks  **[plan first]**

```
Read CLAUDE.md. Implement backend/vision/face.py.

Head crop: derive the face region from pose keypoints (nose, eyes, ear bases) with padding. Do not
add a separate face detector unless the keypoints prove unreliable.

Landmarks: first look for a pretrained dog facial-landmark model compatible with DogFLW's landmark
scheme (the DogFLW authors' release, or anything verifiable). If none exists or it won't run here,
write scripts/train_face_landmarks.ipynb for Colab: fine-tune a small pretrained backbone (e.g. a
timm ResNet-18 or MobileNet) on the DogFLW Kaggle dataset, either heatmap-based or direct
regression, whichever is quicker to get working; export weights that load on MPS/CPU. Keep training
under ~1 hour on a free Colab GPU. Tell me which path you took and why.

face.py returns landmarks in full-frame coords, or None if the face isn't visible/confident.
Also expose the indices we need downstream: mouth corners, upper/lower lip or jaw points, eyes,
ear points, as named constants.

Debug video out/debug_face.mp4.
```

---

## Step 6 — Feature extraction

```
Read CLAUDE.md, especially the FrameEvent features and "Fusion logic → Rules".

Implement backend/vision/features.py. Keep a rolling ~3 s window of keypoints per dog and compute:
- tail_height (-1..1): tail tip relative to the tail-base/back line, normalised by body length.
- tail_wag_hz: dominant frequency of the tail tip's lateral oscillation over the window (FFT or
  zero-crossings after detrending); null if too few confident points.
- ear_position: up | neutral | back | unknown, from ear tip vs ear base vs eye line.
- mouth_open (0..1): jaw/lip landmark distance normalised by face size.
- body_lowering (0..1): withers/hip height vs the dog's own recent "standing" baseline.
- motion_energy (0..1): normalised keypoint displacement across the window.
- in_feeding_zone: from detect.py.

Rules: every feature is null when its inputs are missing or below the confidence threshold. Normalise
by body size so camera distance doesn't matter. Apply light smoothing. Everything tunable lives in
config.yaml.

Build FrameEvent objects from these. Unit tests with synthetic keypoint sequences: a sinusoidal tail
at 2 Hz must measure 2 Hz ±0.3; a tucked tail must give tail_height < -0.5; missing ears -> unknown.
```

---

## Step 7 — Audio events

```
Read CLAUDE.md, especially the AudioEvent contract and the YAMNet classes listed under Datasets.

Implement backend/audio/yamnet_events.py:
- Load YAMNet from TensorFlow Hub once; verify it runs on macOS arm64.
- Consume 16 kHz mono chunks from sources.py and score ~0.96 s windows with 50% hop.
- Map AudioSet classes to our labels: Bark/Bow-wow -> bark, Yip -> yip, Growling -> growl,
  Whimper (dog) -> whimper, Howl -> howl; low overall energy -> silence; everything else -> other.
  Take the max score over the mapped classes. Put the mapping and the threshold in config.yaml.
- Emit AudioEvent objects; debounce so one bark doesn't produce five events.

Test data: download the ESC-50 dataset from GitHub, take the "dog" class clips, and check we detect
bark on most of them. Report the hit rate and a few false positives from non-dog classes.
```

---

## Step 8 — Rules engine

```
Read CLAUDE.md, "Fusion logic → Rules" and the emotion vocabulary.

Implement backend/fusion/rules.py:
- Input: the latest FrameEvent features + AudioEvents from the last ~3 s + a treat_event_recent
  flag (Person B's treat button will set this; accept it as an argument).
- Output: RulesLabel with a score per emotion (0..1), the top emotion and a confidence.
- One small, readable scoring function per emotion, weights and thresholds from config.yaml. Null
  features contribute nothing (they don't count as evidence either way).
- "disinterested" needs duration: in zone, low motion, for longer than a config threshold.
- "aggressive" is capped at moderate confidence unless a growl is present.
- Hysteresis: the top label must win for ~1.5 s before it changes, to stop flicker.
- No dog for > 2 s -> unknown.

Write a docstring table explaining each rule in plain English (Person B and the judges will read it).
Tests: synthetic feature windows for each emotion produce that emotion; flicker input stays stable.
```

---

## Step 9 — Phone (browser) source  **[new]**

```
Read CLAUDE.md, especially "Phone camera (primary live source)" and "The handoff interface".
Steps 0–8 are done; don't touch their behaviour except where this step says so.

Add a fourth source type, browser, to backend/sources.py. The phone's browser captures camera and
mic; Person B's /ingest WebSocket hands the data to us through Pipeline.ingest_frame and
Pipeline.ingest_audio. We never open a socket ourselves.

Implement BrowserSource with the same interface as the other sources:
- push_frame(jpeg: bytes, ts: float): O(1) and non-blocking. Store only the newest JPEG in a
  bounded slot; decode (cv2.imdecode) in our consumer thread, never in the push call.
- push_audio(pcm16: bytes, sample_rate: int, ts: float): append to a bounded ring buffer (~5 s,
  drop oldest). The consumer converts Int16 -> float32, resamples to 16 kHz mono
  (scipy.signal.resample_poly) and yields chunks exactly like the mic source, with timestamps
  derived from ts (server receive time, same clock as every other source).
- Frames faster than the target fps are dropped (keep newest). Portrait frames must work: never
  assume landscape anywhere in detect/pose/face/features; check and fix any place that does.
- Stall detection: no frame for > 2 s -> state "stalled" (exposed later through Pipeline.status());
  recovers automatically when frames resume.
- Push methods never raise to the caller: malformed JPEG or odd-length PCM is logged (rate-limited)
  and dropped.

Also write scripts/fake_phone.py: simulates the phone by reading a video file (+ optional audio file)
and calling the push methods in real time, JPEG-encoding at ~640 px long side and sending 48 kHz
Int16 audio in ~100 ms chunks, with an optional --portrait flag that rotates frames. This lets me
test the browser path without Person B.

Tests: 100 frames pushed instantly -> consumer only sees the newest at the target fps; 48 kHz input
-> 16 kHz chunks of the right length with continuous timestamps; a malformed JPEG is dropped without
an exception; stall triggers after 2 s and clears on the next frame; a portrait clip produces valid
FrameEvents.
```

---

## Step 10 — Wire the Pipeline

```
Read CLAUDE.md and backend/pipeline.py.

Implement Pipeline end to end: sources -> detect -> pose -> face -> features -> FrameEvent,
audio -> AudioEvent, both -> rules -> RulesLabel. Call on_frame_event, on_audio_event and
on_rules_label as specified. latest_frame_jpeg() returns the newest raw frame as JPEG (Person B draws
the overlay from keypoints, so do not burn the skeleton into it).

backend/pipeline.py is shared with Person B. Update it to the exact interface in CLAUDE.md, then
implement the new methods:
- mark_treat(ts): sets the rules engine's treat_event_recent flag for data.rules.treat_window_s
  (default 10 s; add it to config.yaml). Rules already accept the flag as an argument from Step 8;
  only the wiring is new.
- ingest_frame / ingest_audio: forward to BrowserSource. If the source isn't browser, no-op and log
  one warning. Never block, never raise.
- status(): {"source", "state": running|stalled|stopped, "fps", "last_frame_age_s", "audio_ok"}.

Constraints: vision and audio run concurrently (threads or asyncio, your call, justify it); never block
the caller's event loop; drop frames rather than fall behind real time; stop() shuts everything down
cleanly. Log per-stage latency every 5 s.

Add scripts/run_pipeline.py:
  --source file|webcam|stream|browser --path ... --audio-override ... --fast
  (browser: drives the pipeline in-process through scripts/fake_phone.py using --path as the clip)
  --jsonl out/events.jsonl   (writes every event, one JSON per line, with a "type" field)
  --debug-video out/debug.mp4 (skeleton, face points, current rules label and scores drawn)

Done when a stock clip runs end to end in both file and browser mode, events.jsonl validates against
contracts.py, and a treat marked mid-clip visibly raises the "excited" score.
```

---

## Step 11 — Fallback data pack

```
Read CLAUDE.md, "Fallback footage".

I will download 3-5 freely licensed dog clips from Pexels/Pixabay myself and put them in
data/fallback/raw/. Build the rest:
1. data/fallback/SOURCES.md template (file, source URL, licence, what the clip shows); fill in
   whatever you can infer from filenames and leave the rest marked TODO for me.
2. scripts/mux_audio.py: overlay ESC-50 dog clips onto a video at given timestamps with ffmpeg,
   keeping the original audio at a lower volume if present. Output to data/fallback/clips/.
3. scripts/precompute_events.py: run the pipeline in --fast mode on every clip in
   data/fallback/clips/ and write data/fallback/events/<clip>.jsonl plus a debug video each.
4. A manifest data/fallback/manifest.json listing clip, events file, duration, and the expected
   dominant emotion (I'll fill that in) so Person B's demo mode can load it.

Also add treat timestamps to the manifest (manual list per clip) so replayed treat events line up.
```

---

## Step 12 — Tuning pass

```
Read CLAUDE.md and backend/fusion/rules.py.

Build scripts/tune.py: for each fallback clip, plot a timeline (matplotlib, saved PNG) of every
feature, audio events and the per-emotion rule scores, with the expected emotion from the manifest
marked. Print a short table: expected vs dominant predicted label per clip, % of time each label held.

Then look at the plots with me: propose specific threshold/weight changes in config.yaml with the
reasoning, one clip at a time. Do not change code logic without asking; tuning happens in config.
```

---

## Step 13 — Hardening and handoff

```
Read CLAUDE.md. Final pass on the data side before feature freeze.

1. Degradation: pose model fails -> still emit FrameEvents with detection + motion_energy and null
   pose features; face fails -> mouth/ear features null; mic fails -> no AudioEvents, no crash;
   phone disconnects mid-session (kill fake_phone.py) -> status() shows stalled, rules go to unknown
   after 2 s, and everything resumes on reconnect without a restart. Test each by forcing the failure.
2. Performance: measure fps and CPU on the M2 for 5 minutes in browser mode (fake_phone.py at the
   real fps) and in webcam mode. If below 5 fps,
   propose the cheapest fix (smaller model, pose every other frame with interpolation, lower input
   size) and apply the one I pick.
3. Write docs/DATA_HANDOFF.md for Person B: how to construct and run Pipeline, the ingest_* and
   mark_treat contracts, what status() states mean, callback frequencies,
   what null means for each feature, where fallback events live, known weaknesses.
4. Update CLAUDE.md "Commands" with the real data-side commands, and add anything that changed in
   the contracts (tell me first; contract changes need Person B's agreement).
5. Run the full test suite and every fallback clip once more.
```

---

## Handy one-liners for when things go wrong

```
Something in <file> is failing: <paste error>. Find the root cause, explain it in two sentences,
then fix it with the smallest change. Don't refactor anything else.
```

```
The pipeline is under 5 fps. Profile each stage on a 30 s clip and tell me where the time goes
before changing anything.
```

```
Person B says <symptom> when consuming my events. Check events.jsonl and contracts.py against
CLAUDE.md and tell me whose side the bug is on.
```