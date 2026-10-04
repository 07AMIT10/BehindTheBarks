# Android On-Device Perception: Implementation Plan v2

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax. Every task ends in a commit.
>
> **Evidence:** `docs/research/2026-10-02-android-ondevice-evidence.md` (cited below as *EV §n*).
> **Supersedes:** `2026-10-02-android-ondevice.md` (v1). v1 stays for history; §0.2 lists what changed and why.

**Goal.** A Kotlin Android app runs the dog-perception stack continuously on a ~3 GB, NPU-less phone:
- detection → tracking → pose → face → audio → features → rules
- streams the **unchanged** JSON contracts to the existing FastAPI backend, so the dashboard, LLM,
  notifier and demo mode keep working
- holds up for hours without thermal shutdown or being killed by the low-memory killer (LMK).

**Core idea (what's different from v1).** Every behaviour change is built and tuned **in Python first**.
1. A *mobile profile* of the Python pipeline runs the exact `.tflite`/`.onnx` files the phone will ship,
   with fps-invariant features.
2. We retune rules there with the existing `tune.py` / fallback-clip tooling.
3. Kotlin then has to match the **Python mobile profile** (deterministic, testable), not HRNet-W32.

All tuning stays where the tools already are, and Kotlin becomes a port with golden tests.

---

## 0. Before any code

### 0.1 Decisions needed from the team (blocking; proposed defaults in bold)

| # | Decision | Options | Default | Why it blocks |
|---|---|---|---|---|
| D1 | Target device(s) | name 1–2 real phones | **one Helio G85/G99-class + one SD680/SD4-class, both 3 GB** | All latency/thermal numbers are [INF] until measured (EV §2) |
| D2 | Distribution intent | (a) hackathon/demo, non-commercial · (b) store/commercial | **(a) for v1; keep every model swappable so (b) is a model swap, not a rewrite** | YOLO = AGPL, SuperAnimal and DogFLW weights = non-commercial (EV §6) |
| D3 | Pose model | (a) export SuperAnimal (resnet_50/HRNet) · (b) **fine-tune RTMPose-s/t (Apache) on StanfordExtra plus pseudo-labels from SuperAnimal HRNet (distillation)** · (c) AP-10K RTMPose-m (no tail tip or ear tips) | **(b), with (a) as the Phase-0 baseline** | HRNet can't do ≥5 Hz on a low-end CPU. CLAUDE.md allows *only* a face fine-tune today, so (b) needs a CLAUDE.md edit |
| D4 | Tail wag source | pose FFT (today) · **camera-rate ROI motion FFT** | **ROI motion, pose FFT kept as a cross-check** | Nyquist: 5 Hz pose resolves ≤2.5 Hz; the rules want 3–4 Hz (EV §4) |
| D5 | LLM override | keep `llm_override_conf: 0.7` · **LLM narrates only (override off)** | **override off on phone-sourced sessions** | LVLMs are near chance on induced dog states (EV §5) |
| D6 | Server role | **event sink + LLM + notifier** (phone sends events and preview JPEGs) | as default | Matches the pivot decision in agent-memory |

D3 and D5 change project rules or Web-owned config. Agree them with the owner and update `CLAUDE.md` in the same
commit, per the non-negotiables.

### 0.2 v1 plan review: what we keep and what we fix

| v1 item | Problem found | v2 fix |
|---|---|---|
| Golden parity vs Python HRNet (3 px PCK) | The phone can't run HRNet at the needed rate, and rules are tuned to HRNet's noise. Swapping resnet_50 dropped matches 5/7→3/7 | Parity target is the **Python mobile profile** (Phase 1); behaviour acceptance = fallback-clip match table |
| Pose at 1–3 Hz (Task 14) | `tail_wag_hz` needs ≥3.3–6.7 Hz of tail samples. `motion_energy` and EMAs are fps-dependent | Pose ≥5 Hz on tracker crops; wag from ROI motion; time-constant EMAs |
| "class-0 NMS" (Task 7) | COCO dog is class 16 | Dog-only head, or filter class 16; YOLO26 is NMS-free |
| "crop→256×256" (Task 8) | Python passes the padded crop and DLC resizes; the 5 %-outside rejection is missing | Port crop pad, resize/letterbox and outside-crop rejection exactly |
| `app.state.runtime` (Task 16) | Doesn't exist; it's `app.state.rt`. The real Pipeline's watchdog would inject no-dog frames | New `web.pipeline: remote` (no-op pipeline), snapshots from phone JPEGs |
| ByteTrack | Overkill for one dog | One constant-velocity Kalman filter on the box |
| NPU probe via `Accelerator.NPU` | No LiteRT NPU support on Helio G, Unisoc, SD4 or SD680 | CPU (XNNPACK) default; GPU opt-in per model after measurement; NPU code path removed |
| Phone `ts` used as-is | Phone and server clocks differ; `no_dog_unknown_s` and `llm_stale_s` compare against server `time.time()` | Clock-offset handshake; server rebases `ts` |
| FusionState ported to phone | Duplicates the server's FusionState | Phone sends `rules`; server keeps fusion and notify. A Kotlin port only for an offline local-alert mode (Phase 6, optional) |
| Baselines deferred | Strongest-evidenced long-term value (EV §5) | Phase 7: daily aggregates + EWMA/CUSUM |
| No licence or FGS / 16 KB / OEM-killer handling | Blocks distribution or long runs | Built into Phases 0, 2 and 6 |

### 0.3 Global constraints

- **JSON contracts:** `backend/contracts.py` and the CLAUDE.md §JSON contracts **do not change in Phases 0–6**. Kotlin mirrors
  them field for field (`extra="forbid"` semantics: unknown keys are a test failure). Phase 7 proposes
  *additive* contract changes only after sign-off.
- **Emotion vocabulary** is fixed. `RulesLabel.scores` always contains all 8 keys.
- **Demo:** `DEMO_MODE=1`, `backend/demo/` and the mock pipeline stay untouched and green.
- **Models:** no pose or detection training from scratch. Fine-tuning a pretrained RTMPose only if D3(b) is approved.
- **Runtimes:** no NNAPI, no NPU code. LiteRT ≥ 2.1 (16 KB-aligned); ORT ≥ 1.25 / ncnn ≥ 20241226 if used.
- **Execution:** one inference thread pool, **models run serially, never concurrently**.
- **Weights:** non-commercial weights are never committed (they are fetched by script, as `fetch_face_model.py` already does).
  `android/app/src/main/assets/models/` is git-ignored and filled by `scripts/fetch_android_models.py`.
- **Ownership** (CLAUDE.md): Phase 1 touches Data-owned files (`vision/`, `audio/`, `rules.py`). Phase 2 touches
  Web-owned files (`main.py`, `web/`, `config.yaml` web section). Each task names its owner.

---

## 1. Target architecture

```
┌──────────────────────────── Android app (foreground service: camera|microphone) ───────────────────────────┐
│ CameraX ImageAnalysis 640×480 RGBA, KEEP_ONLY_LATEST, AE 15 fps ─┐                                          │
│                                                                  ▼                                          │
│  T0 gate (every frame, <2 ms): luma diff 160×120 + tail-ROI motion signal ──► WagEstimator (FFT, 15 Hz)     │
│        │ activity? / tracker stale?                                                                         │
│        ▼                                                                                                    │
│  T1 detector  2–5 Hz (320² INT8 or fp16) ──► Kalman box tracker (every frame) ──► crop                      │
│  T2 pose      5–8 Hz on tracked crop (RTMPose-s 256×192 fp16/dyn-int8) ──► KeypointMap (19 canonical)        │
│  T3 face      ≤1–2 Hz, only if head box valid (dog_face_landmarks 384² fp16)                                 │
│  T4 audio     AudioRecord 16 kHz ─► RMS/flux gate ─► YAMNet 15600-sample window, 0.48 s hop ─► debounce      │
│        ▼                                                                                                    │
│  FeatureExtractor (time-based EMAs) ─► RulesEngine ─► envelopes {frame|audio|rules|treat}                   │
│  Scheduler + ThermalGovernor + MemoryGuard  ·  Metrics (per-stage ms, fps, PSS, thermal, battery)           │
│  EventUploader: WS /ingest-events (text envelopes + 0x01 preview JPEG 2 fps) + disk spool                   │
└──────────────────────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                                       ▼
   FastAPI (web.pipeline: remote) ─► Runtime.on_frame/on_audio/on_rules ─► FusionState ─► LLM (narrate) ─► Notifier
                                                                       └─► Hub ─► dashboard (unchanged)
```

### 1.1 Budgets (starting targets; Phase 0 replaces them with measurements)

| Stage | Rate | Est. cost on 2×A75/A76 big cores [INF] | Share of 2 big cores |
|---|---|---|---|
| T0 gate + ROI motion | 15 Hz | ≤2 ms | ~2 % |
| Detector 320² | 3 Hz (5 Hz when no track) | 40–80 ms | 6–12 % |
| Kalman | 15 Hz | ≤0.1 ms | ~0 |
| Pose RTMPose-s | 6 Hz | 25–50 ms | 8–15 % |
| Face 384² | 1 Hz | 80–200 ms | 4–10 % |
| YAMNet | ≤2 Hz, gated | 20–40 ms | 1–4 % |
| Features + rules | 6 Hz | ≤1 ms | ~0 |
| Preview JPEG 320 px | 2 Hz | 5–10 ms | ~1 % |
| **Total** | | | **~25–45 %, leaving room for thermal throttling** |

- **Memory budget:** engine + models + buffers ≤ **250 MB PSS**, app total ≤ **400 MB PSS**.
- **Hard ceilings:** `getMemoryClass()` and LMK on the device.
- **Power target:** report it; the camera alone is about 1 W (EV §2), so inference should add ≤ 30 % on top.

---

## Phase 0: Device spike and model bake-off (decision gate, ~2 days)

Nothing after Phase 0 starts until its exit report exists. It turns every [INF] above into a number.

### Task 0.1: Export candidate models (Data)

**Files:** create `scripts/export_android_models.py` and `tests/data/test_export_android_models.py`; modify `requirements-data.txt`.

- [ ] **Step 1: write the failing test.** `--dry-run` lists the candidates and an export calls the right backend per format.
  Mock the heavy converters and assert that the report JSON schema is
  `{name: {format, path, input_shape, dtype, sha256, export_ok, notes}}`.
- [ ] **Step 2: run it.** `pytest tests/data/test_export_android_models.py -v`. Expect FAIL.
- [ ] **Step 3: implement the candidates.**

  | Name | Source | Formats |
  |---|---|---|
  | `det_yolo26n_320` | `ultralytics` export, `format=litert`, imgsz 320, int8 + fp16; `classes=[16]` is not supported at export, so filter in post | `.tflite` |
  | `det_effdet_lite0_320` | MediaPipe model zoo int8 | `.tflite` (download) |
  | `det_picodet_s_320` | PaddleDetection ONNX | `.onnx` |
  | `pose_superanimal_resnet50` | wrap the DLC torch module (the baseline, to quantify the gap) | `litert_torch` → `.tflite`, else ONNX |
  | `pose_rtmpose_s_ap10k` / `pose_rtmpose_m_ap10k` | mmpose/rtmlib ONNX | `.onnx` (+ `onnx2tf` → `.tflite` attempt) |
  | `face_dog_landmarks_384` | existing `dog_face_landmarks_full.tflite` | copy |
  | `audio_yamnet` | MediaPipe `yamnet.tflite` float32 (15600 input). Also try TF-Hub `lite-model/yamnet/classification/tflite/1`; keep whichever loads and has 521 outputs | `.tflite` |

  Pin `litert-torch` and `onnx2tf` in `requirements-data.txt`, in a commented optional block, so the main env isn't broken.
- [x] **Step 4: run tests and the real export.** Run `python scripts/export_android_models.py --out out/android_models`.
  Record which exports failed. A failure is a result here, not a blocker.
- [x] **Step 5: commit** `feat(android): candidate model export + report`.

### Task 0.2: Python-side accuracy of each candidate on fallback clips (Data)

**Files:** create `scripts/bakeoff_models.py` and `tests/data/test_bakeoff_models.py`.

- [x] **Step 1: write the failing test.** Using a 10-frame synthetic clip and stub runners, the bakeoff writes a CSV with columns
  `model, clip, frames, det_recall_vs_ref, mean_iou_vs_ref, kp_pck@0.1_vs_ref (per canonical kp), tail_tip_visible_rate, face_ok_rate, ms_mean_cpu`.
- [x] **Step 2: implement.**
  - The reference is the current server stack (YOLO11s@640 + HRNet-W32) on the 7 fallback clips. Its outputs come
    from re-running `precompute_events.py` internals, not from re-tuned rules.
  - Each candidate runs via `ai_edge_litert` / `onnxruntime` in Python with the **exact** exported file, one thread
    (as a rough proxy only; phone timing comes from 0.3).
  - Per-keypoint PCK matters most for `tail_base`, `tail_tip`, `withers`, `hip` and the ear points.
- [x] **Step 3: run it.** `python scripts/bakeoff_models.py --clips all --out out/bakeoff.csv`.
- [x] **Step 4: commit.**

### Task 0.3: On-device micro-benchmarks (Web/Data pair, on D1 phones)

**Files:** create `scripts/android_bench.sh` and `docs/android/BENCH_PROTOCOL.md`.

- [x] **Step 1: write the protocol and script.**
  - Push models with `adb push`.
  - Run LiteRT's prebuilt `benchmark_model` (Android arm64 binary from the LiteRT release) for each `.tflite` with
    `--num_threads={1,2,4}`, `--use_xnnpack=true`, and `--use_gpu=true` where it loads.
  - Run `onnxruntime_perf_test` for the `.onnx` files (XNNPACK EP, `intra_op_num_threads=1`).
  - Capture warm mean/p95, init time and `dumpsys meminfo` PSS delta.
  - Pin to big cores with `taskset` where `benchmark_model` allows it, and record the core map from
    `/sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq`.
- [x] **Step 2: sustained run.** 10 minutes of looping detector + pose at the §1.1 rates, logging
  `dumpsys thermalservice` every 10 s and noting when throttling starts.
- [x] **Step 3: camera-only baseline.** A 20-minute run of a minimal CameraX analyzer that does nothing, measuring battery %/h and
  temperature. This becomes the "zero-inference" floor.
- [x] **Step 4: write `docs/android/PHASE0_REPORT.md`.**
  - A table per device × model × backend × threads.
  - Accuracy from 0.2.
  - The chosen detector, pose and face backends, each with one paragraph of justification.
  - Updated §1.1 budgets.
  - Answers to D1–D4.
- [x] **Step 5: commit.**

**Exit criteria.**
- The chosen detector + pose fit ≤ 45 % of 2 big cores at the §1.1 rates on the weaker D1 phone.
- PSS ≤ 250 MB.
- If they don't fit, the report must name the degrade: lower pose rate with wag still from ROI, or RTMPose-t.

### Task 0.4 (only if D3(b) is approved): distil a dog RTMPose (Data)

**Files:** create `scripts/pose_distill/` (`make_pseudolabels.py`, `train_rtmpose_dog.py`, `README.md`) and `tests/data/test_pseudolabels.py`.

- [ ] **Step 1: pseudo-labels.** Run SuperAnimal HRNet-W32 (the teacher) over StanfordExtra images plus fallback-clip frames plus any
  CC-licensed dog video frames. Keep keypoints with conf ≥ 0.6, mapped to the canonical 19 via `keypoint_map.py`.
  Merge with StanfordExtra's human tail and ear labels where they exist (human labels win).
  - Test: the mapping is lossless on canonical names, and confidence filtering is applied.
- [ ] **Step 2: fine-tune.** Fine-tune RTMPose-s, initialised from AP-10K RTMPose-m or a COCO RTMPose-s backbone, with a 19-kp SimCC
  head at 256×192, using mmpose configs. Validate on a **dog-identity-disjoint** split. Report per-keypoint PCK@0.1,
  especially tail_tip and ear tips.
- [ ] **Step 3: export** through Task 0.1 and re-run 0.2 and 0.3.
- [ ] **Step 4: licence note.** The teacher is non-commercial, so the distilled weights inherit that risk. Record this in
  `data/fallback/SOURCES.md`-style `docs/android/MODEL_LICENSES.md`.
- [ ] **Step 5: commit** the code only; the weights are fetched or kept out of git.

---

## Phase 1: Python "mobile profile" (Data owner; makes the target deterministic)

### Task 1.1: Make features fps-invariant

**Files:** modify `backend/vision/features.py` and `backend/vision/detect.py`; tests in `tests/data/test_features.py` and `tests/data/test_detect.py`.

- [ ] **Step 1: write failing tests.**
  - The same synthetic sinusoidal keypoint stream, sampled at 4, 6, 8 and 15 Hz, gives
    `tail_height` / `mouth_open` / `body_lowering` within 5 % of each other after 3 s.
  - `motion_energy` is within 10 % at 5 Hz vs 10 Hz for identical true motion plus fixed-σ jitter.
  - The detector EMA reset uses seconds, not frame counts.
- [ ] **Step 2: implement.**
  - `_Ema` gets a `tau_s` and computes `alpha = 1 - exp(-dt/tau_s)`. Derive the default `tau_s` from the current alpha at
    8 fps so 8 fps behaviour is unchanged: `tau = -0.125/ln(1-α)`.
  - Detector: `ema_reset_misses` becomes `ema_reset_s`, defaulting to 8/8 fps = 1.0 s; the bbox EMA is also tau-based.
  - `motion_energy`: resample keypoints onto a fixed 5 Hz grid before differencing, so jitter/dt doesn't scale with fps.
  - Keep the config keys backwards-compatible: if the old key is present, convert it and log once.
- [ ] **Step 3: check the fallback match table.** Run the full suite plus `precompute_events.py` + `tune.py` on the server profile. The
  match table must not regress (still ≥ 4/7, same clips).
- [ ] **Step 4: commit** `refactor(features): time-based smoothing (fps-invariant)`.

### Task 1.2: ROI-motion tail-wag estimator

**Files:** create `backend/vision/wag.py` and `tests/data/test_wag.py`; modify `features.py` (with a `wag_source: pose | roi | both` config) and `config.yaml` (`data.features.wag`).

- [ ] **Step 1: write failing tests.**
  - A synthetic 64×64 ROI with a bar oscillating at f ∈ {1, 2, 3, 4, 6} Hz, sampled at 15 fps for 3 s, gives an estimate
    within ±0.3 Hz.
  - A still ROI with noise returns 0.0.
  - Whole-body translation (the dog walking) suppresses the wag, because the ROI signal is computed relative to the box.
- [ ] **Step 2: implement.**
  - The ROI is a box-relative region around `tail_base`, taken from the last pose (≤ 1 s old) or, as a fallback, the rear 35 % of
    the bbox on the side away from the head.
  - Per camera frame, compute the signed horizontal intensity-centroid shift of `|frame_t − frame_{t−1}|` inside the ROI,
    after subtracting the box's Kalman velocity.
  - Then use the same FFT/peak/periodicity logic as `features.py:_wag` (reuse it), with a 1–8 Hz band and a 20° equivalent
    amplitude gate (EV §3).
  - It runs at camera rate on grayscale in ≤ 2 ms (budget is checked in 0.3).
- [ ] **Step 3: wire it in.** In `Pipeline`, the file/webcam/browser sources call `wag.push(gray_roi, ts)` every frame, even on frames
  where pose is skipped. That needs a `pose_every_n` / `pose_hz` knob in `pipeline.py` to emulate the phone cadence.
- [ ] **Step 4: tune and validate.** Run `tune.py` with `wag_source: both` and plot ROI vs pose wag on the clips. Hand-label wag
  on/off and frequency for 60 s of footage (two raters), and report agreement in `docs/android/WAG_VALIDATION.md`.
  **This is a novel method (EV §3 [U]), so it must be validated before rules depend on it.**
- [ ] **Step 5: commit.**

### Task 1.3: Mobile runners in Python

**Files:** create `backend/vision/mobile_runners.py` and `tests/data/test_mobile_runners.py`; modify `detect.py` (with `detect.backend: ultralytics | tflite | onnx`), `pose.py` (with `pose.backend: dlc | rtmpose_onnx | tflite`), `audio/yamnet_events.py` (with `audio.backend: savedmodel | tflite`) and `config.yaml`.

- [ ] **Step 1: write failing tests.** Each runner returns the **same Python types** as today (`Detection`, canonical keypoint dict,
  AudioEvent scores) on a fixed image or waveform. Pre/post-processing is pinned:
  - **Letterbox:** pad colour 114, scale rounding.
  - **Detector:** class 16 filter, NMS-free decode for YOLO26 or NMS IoU 0.5 for others.
  - **Pose:**
    - RTMPose affine crop with the 1.25 bbox scale and aspect fix to 192:256.
    - SimCC argmax with split ratio 2.0, confidence = max of the x/y SimCC maxima.
    - The 5 %-outside-crop rejection from `pose.py:116-120`.
  - **Audio:** int16 → float32 /32768 and max-pool over frames.
- [ ] **Step 2: implement.**
  - Runners load models through `ai_edge_litert.interpreter` / `onnxruntime`, with one thread for determinism.
  - **Every pre/post-processing constant lives in `backend/vision/mobile_spec.json`.**
  - Kotlin will load the same JSON as an asset, so both sides read one source of truth.
- [ ] **Step 3: add the profile overlay.** `config.mobile.yaml` is a merge-on-top config selecting the backends plus the phone cadence
  (`pose_hz: 6`, `detect_hz: 3`, `face_hz: 1`), and `scripts/run_pipeline.py --profile mobile`.
- [ ] **Step 4: commit.**

### Task 1.4: Retune rules on the mobile profile

**Files:** modify the `config.mobile.yaml` rules section (Data); create `docs/android/RETUNE.md`.

- [ ] **Step 1: regenerate events.** `python scripts/precompute_events.py --profile mobile --out data/fallback/events_mobile/`.
- [ ] **Step 2: tune.** Run `python scripts/tune.py --events data/fallback/events_mobile/` and adjust thresholds, softness and weights.
  Treat it as a fresh calibration and document each change with the clip that motivated it.
- [ ] **Step 3: meet the acceptance bar.**
  - The match table is ≥ the server profile's (currently 4/7), and no clip that matches on the server misses on mobile.
  - Per-feature null-rate is reported; a feature that is null > 50 % on a clip is flagged.
- [ ] **Step 4: optional science-driven rule changes,** behind flags, default off until Phase 7 sign-off:
  - Suppress `aggressive` unless a growl is present (EV §5).
  - Ignore `mouth_open` and `body_lowering` while in the feeding zone with the head down (eating confound).
- [ ] **Step 5: commit.**

### Task 1.5: Golden fixtures for Kotlin

**Files:** create `scripts/export_android_fixtures.py`, `tests/data/test_export_android_fixtures.py` and `tests/android/fixtures/` (checked in, small).

- [ ] **Step 1: write the failing test.** The fixture stream has the kinds
  `{"det","track","pose_in","keypoints","face_in","face","wag_roi","features","audio_pcm_ref","audio","rules"}`,
  with ts monotonic.
- [ ] **Step 2: implement it as three fixture tiers:**
  - **Logic fixtures** (most value; tiny): per frame, canonical keypoints + landmarks + bbox + wag-ROI signal + audio
    events → expected `features` and `rules`. These let Kotlin's `FeatureExtractor` and `RulesEngine` be tested
    **bit-close without any model**.
  - **Model I/O fixtures:** 8 input tensors per model (`.npy` → raw little-endian `.bin` + shape JSON) plus the expected
    output tensors from the Python runner, for checking Kotlin pre/post-processing.
  - **Audio fixtures:** 5 s of PCM16 + the expected YAMNet scores per window + the expected debounced events.
- [ ] **Step 3: export** one 60-frame logic fixture per fallback clip (it must stay under 1 MB each), then commit.

---

## Phase 2: Backend sink and Android skeleton (Web owner, can run in parallel with Phase 1)

### Task 2.1: `web.pipeline: remote` and `/ingest-events`

**Files:** create `backend/web/remote_pipeline.py` and `backend/web/ingest_events.py`; modify `backend/main.py`, `backend/web/runtime.py` (only if needed) and the `config.yaml` web section; tests in `tests/web/test_ingest_events.py`.

- [ ] **Step 1: write failing tests** with the FastAPI `TestClient` WS.
  1. `hello` → `{"type":"hello","proto":1,"device":...,"phone_time":<epoch s>,"models":{...},"profile":"mobile"}`, and the server
     replies `{"type":"hello_ack","server_time":...}`.
  2. Text envelopes `{"type":"frame|audio|rules|treat","data":{...}}` are validated with the pydantic models and reach
     `app.state.rt.on_frame/on_audio/on_rules/treat`. They appear in `hub.history()`.
  3. A `ts` is rebased by the clock offset (`server_time_at_hello − phone_time`, refreshed by a `ping` every 30 s,
     median of the last 5).
  4. A binary `0x01`+JPEG becomes `pipeline.latest_frame_jpeg()`, so `/video` and snapshots work.
  5. An invalid envelope is counted and dropped, and the socket stays open. A bad hello closes with 4400. A second phone
     gets 4409, the same semantics as `/ingest`.
  6. `phone_open`/`phone_close` drive the dashboard's phone status card.
  7. Silence for more than `stale_s` → status `stalled`; the Runtime's no-dog → unknown path fires.
- [ ] **Step 2: implement.**
  - `RemotePipeline` implements `PIPELINE_METHODS`:
    - `run()` awaits forever with no models and **no watchdog**.
    - `latest_frame_jpeg` returns the last phone JPEG.
    - `mark_treat` forwards a `{"type":"treat"}` downlink to the phone, so the phone's rules see `treat_event_recent`.
    - `status()` mirrors phone metrics.
  - Downlink messages are `treat` and `config` (pushes `data.rules` overrides from the server, so tuning doesn't need an APK
    rebuild).
- [ ] **Step 3: add** `make dev-backend-remote` (`WEB_PIPELINE=remote`) and update the CLAUDE.md §Phone camera with the new endpoint and close
  codes (contract doc change, same commit).
- [ ] **Step 4: commit.**

### Task 2.2: Android project scaffold

**Files:** `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/gradle/libs.versions.toml`, `android/app/build.gradle.kts`, `android/app/src/main/AndroidManifest.xml`, `android/.gitignore`, and `Makefile` targets `android-build`, `android-test`, `android-install`.

- [ ] **Step 1:** `cd android && ./gradlew :app:assembleDebug` fails (no project).
- [ ] **Step 2: scaffold.**
  - minSdk 26 (CameraX + thermal listener at 29 is guarded), target/compileSdk 35, Kotlin 2.x, AGP ≥ 8.5.1, NDK r28+
    if any native code (16 KB alignment, EV §2).
  - **Deps** (pin exact versions in `libs.versions.toml` after checking the latest on the day):
    `com.google.ai.edge.litert:litert` (2.x), `onnxruntime-android` (≥ 1.25, only if Phase 0 picked ONNX), CameraX
    `core/camera2/lifecycle` 1.5.x, `kotlinx-serialization-json`, `okhttp`, `kotlinx-coroutines`. Test deps: JUnit 5,
    coroutines-test, OkHttp `mockwebserver`, Truth.
  - `abiFilters "arm64-v8a", "armeabi-v7a"`. The v7a ABI matters on some 3 GB Android Go-ish builds; drop it if
    Phase 0 shows the D1 phones are arm64.
  - **Manifest:** `CAMERA`, `RECORD_AUDIO`, `FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_CAMERA`,
    `FOREGROUND_SERVICE_MICROPHONE`, `POST_NOTIFICATIONS`, `WAKE_LOCK`, `INTERNET`. Service
    `foregroundServiceType="camera|microphone"`.
  - A CI check runs `zipalign -c -P 16 -v 4 app-debug.apk` (16 KB) in `make android-build`.
- [ ] **Step 3: verify.** `./gradlew :app:assembleDebug :app:testDebugUnitTest` → PASS.
- [ ] **Step 4: commit.**

### Task 2.3: Contracts mirror plus generated Kotlin types

**Files:** modify `scripts/gen_ts_types.py` (add a `--kotlin` emitter, or create `scripts/gen_kotlin_types.py`); generated `android/app/src/main/java/com/btb/ondevice/contracts/Contracts.kt`; test `ContractsTest.kt`.

- [ ] **Step 1: write failing tests.**
  - Every logic-fixture `features` and `rules` row round-trips through Kotlin with **identical key sets and nulls kept**
    (`explicitNulls = true`, `encodeDefaults = true`). Unknown keys are rejected (`ignoreUnknownKeys = false`).
  - Floats are emitted with the same JSON number semantics; the comparison is tolerant.
- [ ] **Step 2: implement.** Generate the types from `backend/contracts.py` (the source of truth, the same pattern as the TS types) so the contracts can't
  drift. `Emotion` is a Kotlin enum serialised by lower-case name, and `body_keypoints` is a `Map<String, List<Double>?>`.
- [ ] **Step 3: commit.**

### Task 2.4: Foreground service, CameraX and mic capture (no ML yet)

**Files:** `capture/MonitorService.kt`, `capture/CameraSource.kt`, `capture/MicSource.kt`, `ui/MainActivity.kt` (setup screen: permissions, backend URL, zone guide, start/stop); tests `CaptureConfigTest.kt` (pure logic) plus one instrumented smoke test.

- [ ] **Step 1: write tests.**
  - Pure: fps-range selection picks the lowest available range containing 15, falling back to `[x, 30]` plus
    analyzer throttling.
  - Rotation maths for portrait frames (`rotationDegrees`). Nothing may assume landscape (CLAUDE.md).
- [ ] **Step 2: implement.**
  - **Service:**
    - `MonitorService` is started **from the visible Activity** (while-in-use FGS rule). It shows an ongoing notification
      with a stop action and holds a partial wakelock only while running.
    - CameraX is bound to the **service's** `LifecycleOwner` with `ImageAnalysis` only (no Preview while the screen is off).
      Preview is attached when the Activity is visible.
  - **Camera:** `ImageAnalysis` at 640×480 via `ResolutionSelector`, `OUTPUT_IMAGE_FORMAT_RGBA_8888`, `STRATEGY_KEEP_ONLY_LATEST`
    and a single-thread executor. Close every `ImageProxy` in `finally`.
    - AE fps range via `Camera2Interop` (or CameraX 1.5 `setExpectedFrameRateRange`), plus a software throttle.
    - Timestamps: `imageInfo.timestamp` (monotonic ns), converted to epoch with one offset captured at start
      (`System.currentTimeMillis()` vs `SystemClock.elapsedRealtimeNanos()`).
  - **Mic:** `AudioRecord` with `MIC` source, 16 kHz mono PCM16 and 100 ms reads into a lock-free ring buffer (5 s). If 16 kHz
    is unsupported, use 48 kHz and a polyphase decimator ÷3.
  - **OEM survival:** a settings screen links to the battery-optimisation exemption (`ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`)
    with an explanation.
- [ ] **Step 3: manual check.** The service runs 30 minutes with the screen off on a D1 phone, and analyzer fps is logged. Write the
  result in `docs/android/PHASE2_NOTES.md`.
- [ ] **Step 4: commit.**

### Task 2.5: Event uploader plus the first end-to-end milestone

**Files:** `net/EventUploader.kt`, `net/Spool.kt`, `pipeline/StubPerception.kt`; tests `EventUploaderTest.kt` (MockWebServer) and `SpoolTest.kt`.

- [ ] **Step 1: write failing tests.**
  - The hello/ack handshake and the clock-offset pings.
  - Text envelopes plus binary preview JPEGs (≤ 2 fps, 320 px long side, q 0.6).
  - Backoff 0.5/1/2/4/5 s; no reconnect after 4400 or 4409; a 4408 replacement is honoured.
  - When disconnected, **non-frame** events (audio, rules, treat) are spooled to disk and capped at 5 minutes / 2 MB, then flushed in
    ts order on reconnect. Frame events are dropped, not spooled; the dashboard only needs live frames.
  - Backpressure: if the OkHttp queue exceeds 256 KB, drop the preview JPEG first, then frame events.
- [ ] **Step 2: implement.** `StubPerception` emits contract-valid scripted events, the same idea as `mock_pipeline.py`.
- [ ] **Step 3: run the milestone M1 check.** Phone (stub) → tunnel → `make dev-backend-remote` → dashboard shows the emotion card, the timeline and live
  preview. `python scripts/check_integration.py --seconds 30` passes in remote mode (extend that script if needed).
- [ ] **Step 4: commit.**

---

## Phase 3: Pure-Kotlin logic ports with strict golden parity

No models are involved, so this is fast to test and fully deterministic. Each task is a 1:1 port, tested against Phase 1.5 logic fixtures.

| Task | Kotlin file | Python source | Parity bar |
|---|---|---|---|
| 3.1 | `config/DataConfig.kt` | the `data:` section of `config.yaml` + `config.mobile.yaml` merged at build time into `assets/config_data.json` (by script) | deep-merge semantics identical; test against Python `deep_merge` output |
| 3.2 | `vision/KeypointMap.kt` | `keypoint_map.py` | exact names; conf < 0.3 → null |
| 3.3 | `vision/BoxTracker.kt` | new (Phase 1 Kalman, if added to Python, else the EMA in `detect.py`) | box within 0.5 px; reset-after-seconds identical |
| 3.4 | `vision/WagEstimator.kt` | `wag.py` | Hz within 0.05 on fixtures; same FFT size, window and interpolation. Use a small radix-2 FFT, no deps |
| 3.5 | `vision/FeatureExtractor.kt` | `features.py` | every feature within 1e-4 absolute (same maths, doubles); `ear_position` exact; null-ness exact |
| 3.6 | `fusion/RulesEngine.kt` | `rules.py` | label exact on 100 % of fixture frames; confidence and scores within 1e-4; emulate `round(x,4)` (half-even) and the dict-order tie-break |
| 3.7 | `audio/AudioEventDebouncer.kt` | the debounce part of `yamnet_events.py` | event sequence exact on audio fixtures |
| 3.8 | `pipeline/TierZeroGate.kt` | new (thresholds in `mobile_spec.json`) | unit tests only |

For each task:
- [ ] **Step 1:** write the failing fixture-replay test (`FixtureReplay.kt` helper: reads `tests/android/fixtures/*.jsonl`, made available
  to JVM tests via `sourceSets.test.resources.srcDir("../../tests/android/fixtures")`).
- [ ] **Step 2:** run it with `./gradlew :app:testDebugUnitTest --tests '*<Name>*'` and expect FAIL.
- [ ] **Step 3:** write the port. Keep function names aligned with Python (`_wag` → `wag`), with a header comment pointing to the Python file and commit
  hash it mirrors.
- [ ] **Step 4:** PASS, then commit.

**Drift guard.** Add a test to `tests/data/test_kotlin_port_hash.py` that fails when `features.py`, `rules.py`, `wag.py` or `keypoint_map.py`
change without the Kotlin header hash being updated. That forces the two sides to move together.

---

## Phase 4: Model runners on device

### Task 4.1: `ModelRunner` abstraction

**Files:** `ml/ModelRunner.kt` (interface), `ml/LiteRtRunner.kt`, `ml/OrtRunner.kt` (only if Phase 0 chose ONNX), `ml/ModelRegistry.kt`; tests `ModelRegistryTest.kt` (JVM, fake runner) and `ml/RunnerInstrumentedTest.kt` (androidTest).

```kotlin
interface ModelRunner : AutoCloseable {
    val name: String
    val backend: Backend               // CPU_XNNPACK | GPU | ORT_XNNPACK
    fun run(inputs: Array<ByteBuffer>, outputs: Array<ByteBuffer>)   // caller-owned direct buffers, reused
    fun warmup(n: Int = 3)
}
enum class Backend { CPU_XNNPACK, GPU, ORT_XNNPACK }
```

- [ ] **Step 1: write tests.**
  - Registry: the backend comes from `assets/models/manifest.json` (Phase 0 choice per device class); there is a GPU→CPU fallback on any init
    exception or on output mismatch versus the CPU reference on the warmup input (max abs diff > tolerance in `mobile_spec.json`).
  - Reload-on-trim: under `TRIM_MEMORY_UI_HIDDEN` nothing unloads; on an explicit `MemoryGuard` request, face and audio unload
    (lazily reloaded).
- [ ] **Step 2: implement.**
  - LiteRT `CompiledModel` with `Accelerator.CPU` (XNNPACK) and an explicit thread count from the manifest (default 2, pinned via
    the app's inference thread affinity; see 5.1). Use `Accelerator.GPU` only where the manifest says so.
  - Pre-allocate input and output `TensorBuffer`s once.
  - ORT: `SessionOptions` with `intra_op_num_threads=1`, XNNPACK EP with `threads=2`, and `allow_spinning=0` (EV §2).
  - Models load from assets via memory-map (clean pages, reclaimable).
- [ ] **Step 3: commit.**

### Task 4.2: Detector

**Files:** `vision/DogDetector.kt`, `vision/Letterbox.kt`; tests `LetterboxTest.kt` (JVM) and `DogDetectorInstrumentedTest.kt`.

- [ ] **Step 1:** JVM test: letterbox maths and box un-mapping match `mobile_spec.json` and the Python runner on the 8 model-I/O fixtures (decoded boxes from the
  fixture's *output tensor* within 0.5 px, which checks post-processing without needing the model on the JVM).
- [ ] **Step 2:** instrumented test: the real model on the fixture input tensor gives outputs within the tolerance of the Python output tensor (INT8: exact or ±1 LSB;
  fp16: 1e-2 relative).
- [ ] **Step 3:** implement:
  - RGBA → model input with no Bitmap allocations per frame: a reusable `ByteBuffer`, scaled with a precomputed nearest or
    bilinear map, matching what Phase 1.3 pinned.
  - Class-16 filter (or a dog-only export), NMS-free decode (YOLO26) or a tiny CPU NMS.
  - Selection rule "highest conf rounded to 2dp, tie → larger area" (`detect.py:139`).
- [ ] **Step 4:** commit.

### Task 4.3: Pose estimator

**Files:** `vision/PoseEstimator.kt`, `vision/AffineCrop.kt`; tests in the same JVM + instrumented pattern.

- [ ] **Step 1:** the affine crop matrix (1.25 scale, aspect fix) matches the Python `cv2.getAffineTransform` output to within 1e-6. SimCC decode on the fixture output
  tensors gives keypoints within 0.5 px of Python, and the outside-crop rejection behaves identically.
- [ ] **Step 2:** implement the crop from the **tracked** box (Kalman-predicted when the detector skipped this frame), then the canonical
  mapping through `KeypointMap`.
- [ ] **Step 3:** commit.

### Task 4.4: Face landmarker

**Files:** `vision/FaceLandmarker.kt` (head box port of `face.py:141-171`, warpAffine to 384², plausibility checks `:203-214`); tests in the same pattern.

- [ ] **Step 1:** test that the head box from fixture keypoints matches Python exactly, that the plausibility verdict is identical, and that landmarks are within 0.5 px given the fixture output tensor.
- [ ] **Step 2:** implement; commit.

### Task 4.5: Audio classifier

**Files:** `audio/YamnetClassifier.kt`, `audio/AudioPipeline.kt` (ring buffer → 15600 window, 0.48 s hop, gap > 0.5 s resets, RMS < `silence_rms` → silence and the model is skipped); tests in `AudioPipelineTest.kt` (JVM, fake classifier returns fixture scores) and an instrumented scores-parity test.

- [ ] **Step 1:** test that the window boundaries and ts (end of window) match Python, that the event sequence on the audio fixture is exact, and that real-model scores are within 1e-3 of the Python TFLite runner.
- [ ] **Step 2:** implement the label map from `config_data.json` (score = max over mapped classes, threshold 0.3); commit.

---

## Phase 5: Orchestration, scheduling, thermal and memory

### Task 5.1: `OnDevicePipeline` plus a single inference executor

**Files:** `pipeline/OnDevicePipeline.kt`, `pipeline/InferenceExecutor.kt`; test `OnDevicePipelineTest.kt` (JVM: fake runners replay fixture tensors; asserts envelopes equal the fixture `features`/`rules` sequence).

- [ ] **Step 1:** write the failing replay test.
- [ ] **Step 2:** implement.
  - **One `InferenceExecutor`** is a single thread that runs models **serially**. It sets `Process.setThreadPriority(THREAD_PRIORITY_DISPLAY)`
    and, on API 31+, an ADPF `PerformanceHintManager` session with target work duration = the tick budget, reporting
    actual durations (`setPreferPowerEfficiency(true)` on API 35+).
  - The camera analyzer thread does only T0, ROI wag and enqueueing. If the executor is busy, the frame's model work is
    skipped (latest-wins) but **T0 and wag still run on every frame**.
  - The audio thread does the gate and enqueues YAMNet jobs onto the same executor at **lower priority** than pose and higher
    than face.
- [ ] **Step 3:** commit.

### Task 5.2: Scheduler

**Files:** `pipeline/Scheduler.kt`; test `SchedulerTest.kt` (fake clock).

```kotlin
data class Cadence(val detectHz: Double, val detectHzNoTrack: Double, val poseHz: Double, val faceHz: Double, val audioMaxHz: Double)
data class Tick(val detect: Boolean, val pose: Boolean, val face: Boolean)
class Scheduler(private var cadence: Cadence, private val clock: () -> Double) {
    fun onFrame(ts: Double, gate: GateState, track: TrackState, headBoxValid: Boolean): Tick
    fun setLevel(level: DegradeLevel)          // from ThermalGovernor / MemoryGuard
}
```

- [ ] **Step 1: write tests.**
  - **Gating:**
    - No dog and no motion for 10 s → detect drops to 1 Hz ("idle watch") and pose and face stop.
    - Motion returns → detect at `detectHzNoTrack` on the next frame.
  - **Cadence:**
    - Detect runs when the track is stale (> 1/detectHz) or the tracker's innovation is large (the IoU of the predicted vs the
      last detected box < 0.5).
    - Pose never exceeds `poseHz`, and never runs without a track younger than 0.5 s.
    - Face runs only if `headBoxValid` (`min_head_points`, `min_crop_px`).
  - **Priorities:** if the budget is exceeded, face is skipped first, then detect is stretched; pose is never skipped while
    feature validity needs it, which keeps the `motion_energy` 5 Hz grid fed.
- [ ] **Step 2:** implement; commit.

### Task 5.3: Thermal governor and memory guard

**Files:** `pipeline/ThermalGovernor.kt`, `pipeline/MemoryGuard.kt`; tests `ThermalGovernorTest.kt` and `MemoryGuardTest.kt` (fake providers).

Degrade ladder (each level is a `Cadence`; level transitions need 30 s of hysteresis):

| Level | Trigger (any) | detect / pose / face / audio |
|---|---|---|
| L0 normal | headroom < 0.75 and status ≤ LIGHT | 3 / 6 / 1 / 2 Hz |
| L1 warm | headroom ≥ 0.85 or status MODERATE | 2 / 5 / 0.5 / 2 |
| L2 hot | headroom ≥ 0.95 or status SEVERE | 1 / 4 / off / 1; wag still on |
| L3 critical | status CRITICAL+ or battery < 15 % and not charging | 1 / off / off / 1. Emit the `stalled`-style status; the server shows "reduced monitoring" |

- [ ] **Step 1: write tests.**
  - The thermal status listener plus `getThermalHeadroom(10)` are polled **no faster than every 10 s** (EV §2: faster returns NaN). NaN or
    unsupported → status-only mode.
  - Hysteresis is respected.
  - **MemoryGuard:**
    - Reads `ActivityManager.getMemoryInfo().lowMemory` and its own `Debug.getPss()` every 30 s.
    - Above the budget, or on `TRIM_MEMORY_BACKGROUND`, it unloads the face model then the audio model, and records the reason
      in `status()`.
- [ ] **Step 2:** implement. Note that pose at L2 = 4 Hz is the floor that keeps `motion_energy` valid; wag no longer depends on pose
  (ROI motion), which is the main reason for D4.
- [ ] **Step 3:** commit.

### Task 5.4: Metrics, status and the on-phone debug overlay

**Files:** `bench/Metrics.kt`, `ui/DebugOverlay.kt`.

- [ ] **Step 1:** record per-stage ms mean/p95, realised Hz per stage, executor queue drops, PSS, thermal status/headroom, battery %,
  charging, degrade level, and backend per model.
- [ ] **Step 2:** sinks:
  - `status()` → sent every 2 s to the server (reusing the phone-status card) and to a JSONL in app files, so
    `adb pull` gets it for the soak.
  - Debug overlay (when the Activity is visible): bbox, skeleton, wag ROI and the current label.
- [ ] **Step 3:** commit.

---

## Phase 6: Validation, soak and demo hardening

### Task 6.1: End-to-end parity on device (clip replay mode)

**Files:** `capture/ClipSource.kt` (decodes a bundled fallback clip with `MediaExtractor`/`MediaCodec` at the clip's fps and feeds the analyzer path; replaces the camera), `scripts/android_parity.py`.

- [ ] **Step 1:** run each fallback clip on the phone in replay mode with `--fast`-like deterministic cadence (scheduler fixed, no thermal).
  Pull the JSONL.
- [ ] **Step 2:** run `scripts/android_parity.py` against the Python mobile profile on the same clip. The bars are:
  - box IoU ≥ 0.9 (median);
  - keypoint PCK@0.05 ≥ 0.95;
  - features within the tolerances from Phase 3;
  - rules label agreement ≥ 97 % of frames;
  - **match table identical.**
- [ ] **Step 3:** run the same with the real scheduler (thermal on) and report the label-agreement drop. Above 10 % means re-tuning cadence.
- [ ] **Step 4:** commit the parity report to `docs/android/PARITY.md`.

### Task 6.2: Soak, power and thermal

**Files:** extend `scripts/android_bench.sh` with `soak`; `docs/android/SOAK.md`.

- [ ] **Step 1:** 2 h screen-off live soak on each D1 phone, with a dog clip played on a monitor or the real dog.
- [ ] **Step 2:** the acceptance bars are:
  - no FGS kill or ANR;
  - PSS ≤ 400 MB and flat (< 10 MB/h growth);
  - ≥ 95 % of the time at L0–L1;
  - feature validity ≥ 80 % of dog-present frames;
  - battery drain reported against the Phase 0.3 camera-only floor (target ≤ +30 %);
  - phone-to-dashboard median latency ≤ 1.5 s.
- [ ] **Step 3:** test resilience:
  - Wi-Fi off for 2 minutes → spool flush correct.
  - Backend restart → reconnect.
  - Camera permission revoked mid-run → `camera:false` status plus the camera-blocked card (same semantics as `/camera`).
- [ ] **Step 4:** commit.

### Task 6.3: Demo integration and runbook

**Files:** `docs/DEMO_RUNBOOK.md` (an "Android app" section), CLAUDE.md (Architecture, Phone camera, Commands, Repo layout), README link.

- [ ] **Step 1:** document the order of fallbacks:
  1. Android app → `/ingest-events`.
  2. Browser `/camera` page → `/ingest` (unchanged; server inference).
  3. `DEMO_MODE=1` offline clips (unchanged).

  The switch must take under 30 s; rehearse it twice.
- [ ] **Step 2:** optional offline local alerts: a Kotlin `FusionState` port (from `state.py`, golden-tested like Phase 3) posts a local Android
  notification on `fearful` / appetite-flag changes when the server is unreachable. Only if time allows.
- [ ] **Step 3:** commit.

---

## Phase 7 (v1.1): Behaviour indicators, personal baselines, honest output (needs contract sign-off)

The evidence (EV §5) says the defensible outputs are **cues and deviations from this dog's own baseline**, not
one-word emotions. Phase 7 adds them **additively**, keeping the existing contract fields so the dashboard and rules keep working.

### Task 7.1: Contract proposal (both owners; CLAUDE.md updated in the same commit)

- `FrameEvent.features` gains optional `visibility: {tail, ears, face, mouth}`, each a 0–1 fraction over the last 3 s,
  and `head_down_in_zone: bool | null`.
- New `IndicatorEvent` (type `"indicator"`, ~1 Hz):

  ```json
  {"ts": 0, "arousal": "low|medium|high|unknown", "valence": "positive|negative|uncertain", "cues": [{"cue": "ears_back", "conf": 0.6, "visible": 0.8}]}
  ```

  `valence` defaults to `uncertain` unless ear cues and context (treat or meal) agree.
- New `BaselineEvent` (type `"baseline"`, on change): `{ts, metric, value, baseline_mean, z, cusum, window: "day|hour", alert: bool}`.
- `EmotionState.reason` stays. The dashboard shows "possible X" plus the cues; that's a UI wording change, not a contract change.

### Task 7.2: Eating-bout and appetite detection (Python first, then Kotlin)

- **Eating:** in zone + head-down posture (nose below withers by more than k body lengths) + low translation, for ≥ 5 s, means an eating bout.
- **Validation:** against two-rater video codes on fallback clips plus new owner clips; report sensitivity, specificity and κ.
- **Appetite flag:** no eating bout within N minutes of a meal marker (treat button or schedule) versus the dog's baseline. This
  replaces "disinterested"-as-emotion in the UI copy.

### Task 7.3: Personal baseline store and anomaly detection (Kotlin, on phone)

**Files:** `baseline/BaselineStore.kt` (Room/SQLite: per-day and per-hour aggregates: bowl visits, eating minutes, active minutes,
vocalisation counts by label, cue rates), `baseline/Anomaly.kt`; tests with synthetic 30-day series.

- EWMA mean and variance per metric × hour-of-day bucket, with a ≥ 7-day warm-up before any alert.
- One-sided CUSUM on daily aggregates; an alert needs persistence over ≥ 2 days and is rate-limited to 1/day per metric.
- **Never a diagnosis.** The copy is "Bruno ate 40 % less than usual for 2 days; consider checking with your vet if it continues".
- Expect high false-positive rates (EV §5: Wagner 66 %). Ship with conservative thresholds and an owner "this was fine" feedback button,
  logged for later calibration.

### Task 7.4: Validation protocol (people, not code)

- Use 5–10 dogs, owner-logged context, and two blind raters on a fixed ethogram (ear position, tail position, lowering, lick, vocal type,
  eating). Report κ or α per cue.
- Report per-cue sensitivity and specificity **by visibility bucket**, leave-one-dog-out, broken down per breed and ear type.
- The one affect contrast with real support is a **treat-anticipation vs blocked-access** session using the treat button (Bremhorst paradigm). Run it
  as the headline validation.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation / trigger |
|---|---|---|---|
| Chosen pose model too slow on the weaker D1 phone | Med | High | Phase 0 gate; RTMPose-t; pose 4 Hz floor; wag decoupled from pose |
| Distilled or AP-10K pose loses tail_tip or ears | High | Med | ROI wag; ears from face landmarks (already preferred in `features.py`); null-tolerant rules (`min_coverage`) |
| Retuned rules match < 4/7 | Med | High | Phase 1.4 gate before any Kotlin model work. If it fails, keep server inference for the demo (browser `/camera`) and ship the phone as sensor-only |
| INT8 accuracy loss (detector about 15 % relative mAP in the literature) | Med | Med | Bake-off measures it; fall back to fp16 or w8a16; the large single dog tolerates lower mAP |
| GPU delegate wrong or slow on Mali/Adreno low-end | High | Low | CPU default; GPU only with output-diff check at warmup |
| OEM kills the FGS with the screen off | Med | High | Battery-optimisation exemption; restart-on-open; status gap visible on the dashboard; 2 h soak |
| Clock skew breaks the server timers | High if ignored | Med | Handshake + ping offset (2.1) |
| Licence blocks distribution | Certain for (b) | High for (b) | D2; model registry makes it a swap: PicoDet/EffDet + RTMPose (non-SuperAnimal labels) + a commercially licensed face model |
| Python and Kotlin logic drift | Med | Med | Generated contracts; header hash guard (Phase 3); shared `mobile_spec.json` / `config_data.json` |
| ROI-wag method doesn't validate | Med | Med | Two-rater validation in 1.2; fall back to pose FFT at higher pose Hz with `wag_fast` thresholds lowered to what's resolvable |

## Milestones

| Milestone | Scope | Demo-able? |
|---|---|---|
| M0 | Phase 0 report: numbers on real phones, D1–D4 answered | – |
| M1 | Phase 2.5: stub phone → remote backend → dashboard | yes (fake data) |
| M2 | Phase 1.4: Python mobile profile retuned, ≥ 4/7 | yes (server-side, mobile models) |
| M3 | Phase 3 + 4: all Kotlin ports green against golden fixtures | – |
| M4 | Phase 5 + 6.1: real on-device perception, parity report | **yes, the target demo** |
| M5 | Phase 6.2–6.3: soak passed, runbook rehearsed | yes, robust |
| M6 | Phase 7: indicators + baselines + validation study | product-grade claims |

## Out of scope

- Any NPU path, iOS, an on-device LLM (OOM/thermal risk on 3 GB with vision running), and training detection or pose from scratch.
- Multi-dog tracking.
- Replacing the browser `/camera` path: it stays as the fallback.
