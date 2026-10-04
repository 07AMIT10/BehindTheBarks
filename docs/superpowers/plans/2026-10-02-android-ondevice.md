# Android On-Device Perception Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the dog-perception pipeline (detection → pose → face → audio → rules → fusion) onto a ~3 GB Android phone, streaming the existing JSON contracts to the FastAPI backend so the dashboard keeps working unchanged.

**Architecture:** Kotlin Android app using CameraX + `AudioRecord`, LiteRT 2.x `CompiledModel` runtime (CPU/GPU/NPU with automatic fallback), `litert_torch` for PyTorch-model export (NCNN/PNNX fallback), official quantized YAMNet TFLite. The phone is a *perception producer*; the backend's `Runtime` becomes the consumer via a new WebSocket endpoint. All JSON contracts are unchanged and mirrored in Kotlin with golden-parity tests against the Python reference implementation.

**Tech Stack:** Kotlin, CameraX (camera-core/camera2), kotlinx-serialization, OkHttp WS, LiteRT 2.x (`com.google.ai.edge.litert:litert`), `litert_torch`, NCNN/PNNX (fallback), JUnit + coroutine tests, Gradle.

## Global Constraints

- JSON contracts (`backend/contracts.py`, CLAUDE.md §JSON contracts) do not change; Kotlin mirrors them field-for-field.
- `EmotionState.source` stays `rules | llm | fused`; v1 phone emits `rules` only, LLM still server-side and optional.
- NNAPI delegate is deprecated/removed — never used. NPU only via `CompiledModel(Accelerator.NPU)` with CPU/GPU fallback.
- Timestamps: epoch seconds float; FrameEvent `source` is `"live"` for the phone.
- Demo mode (`DEMO_MODE=1`) and the mock pipeline remain fully functional; nothing in `backend/demo/` changes.
- No from-scratch model training; all weights pretrained/exported.
- Parity tolerances: keypoint coordinates within 3 px PCK on golden crops; rules label must match on >95% of golden frames; features within 5% relative.

---

## Project layout (new)

```
android/
  settings.gradle.kts
  build.gradle.kts
  app/
    build.gradle.kts
    src/main/AndroidManifest.xml
    src/main/java/com/btb/ondevice/
      contracts/Contracts.kt        # Kotlin mirror of backend/contracts.py
      ml/LiteRtRunner.kt            # CompiledModel wrapper, CPU/GPU/NPU probe + fallback
      vision/DogDetector.kt         # YOLO tflite
      vision/PoseEstimator.kt       # DLC SuperAnimal tflite (or NCNN)
      vision/FaceLandmarker.kt      # existing dog_face_landmarks tflite
      vision/KeypointMap.kt         # port of backend/vision/keypoint_map.py
      vision/FeatureExtractor.kt    # port of backend/vision/features.py
      vision/Tracker.kt             # ByteTrack-lite
      audio/AudioEventDetector.kt   # YAMNet tflite + debounce port
      fusion/RulesEngine.kt         # port of backend/fusion/rules.py
      fusion/FusionState.kt         # port of backend/fusion/state.py
      pipeline/TierZeroGate.kt      # motion + VAD gating
      pipeline/Scheduler.kt         # staggered cadence + thermal backoff
      pipeline/OnDevicePipeline.kt  # orchestrates all stages
      capture/CameraXSource.kt      # ImageAnalysis → frames
      capture/MicSource.kt          # AudioRecord → 16 kHz mono
      net/EventUploader.kt          # WS client → backend
      bench/Metrics.kt              # per-stage latency, fps, mem, thermal
    src/main/assets/models/*.tflite (+ *.ncnn.param/*.bin if fallback used)
    src/test/...                    # JVM unit tests (parity + pure logic)
scripts/
  export_android_models.py          # torch/TF → .tflite (litert_torch) or NCNN; parity report
  export_android_fixtures.py        # record golden pipeline outputs from fallback clips
tests/android/fixtures/*.jsonl      # golden frames/features/audio/rules streams
```

---

## Task 1: Export golden fixtures from the Python pipeline

**Files:**
- Create: `scripts/export_android_fixtures.py`
- Test: `tests/data/test_export_android_fixtures.py`

- [ ] **Step 1: Write failing test**

```python
# tests/data/test_export_android_fixtures.py
from pathlib import Path
import json, subprocess, tempfile

def test_fixture_stream_shape(tmp_path):
    out = tmp_path / "fx.jsonl"
    subprocess.run(["python", "scripts/export_android_fixtures.py",
                    "--clip", "data/fallback/clips/eating_home.mp4", "--out", str(out), "--max-frames", "40"], check=True)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert rows, "empty fixture"
    kinds = {r["kind"] for r in rows}
    assert {"det", "keypoints", "face", "features", "audio", "rules"} <= kinds
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/data/test_export_android_fixtures.py -v` → script missing.

- [ ] **Step 3: Implement** `scripts/export_android_fixtures.py` — reuse `backend.pipeline.Pipeline` internals: for each processed frame record `det` (bbox, conf), canonical `keypoints`, `face` landmarks, `features`, plus audio events and rules labels as they arrive; one JSON object per line `{"kind": ..., "ts": ..., "data": ...}`. `--max-frames` caps for CI.

- [ ] **Step 4: Run tests** → PASS; commit one 60-frame fixture per fallback clip into `tests/android/fixtures/`.

- [ ] **Step 5: Commit** `feat: export golden fixtures for Android parity tests`

## Task 2: Export/quantize models for Android

**Files:**
- Create: `scripts/export_android_models.py`
- Modify: `requirements-data.txt` (add `litert-torch`, `pnnx`)
- Test: `tests/data/test_export_android_models.py`

- [ ] **Step 1:** Failing test asserts the script produces the four asset files and a parity report.

```python
def test_exports(tmp_path):
    subprocess.run(["python", "scripts/export_android_models.py", "--out", str(tmp_path), "--quantize", "fp16"], check=True)
    assert (tmp_path / "yolo_dog.tflite").exists()
    assert (tmp_path / "pose_superanimal.tflite").exists() or (tmp_path / "pose_superanimal.ncnn.param").exists()
    assert (tmp_path / "dog_face_landmarks.tflite").exists()
    assert (tmp_path / "yamnet_classification.tflite").exists()
    report = json.loads((tmp_path / "export_report.json").read_text())
    assert report["yolo_dog"]["parity_ok"] is True
```

- [ ] **Step 2–3:** Implement with `litert_torch.convert(model.eval(), sample)`; PT2E INT8 needs calibration set from `data/fallback/` frames (mix of lit, occluded, head-on). For DLC SuperAnimal HRNet-W32, wrap the torch module; if conversion fails after 2 attempts, run the same module through `pnnx.export` and emit `.ncnn.param/.bin` instead. Copy `dog_face_landmarks_full.tflite` from `models/`. Download YAMNet/classification TFLite from TF Hub. Each export validated: bbox/keypoint parity vs PyTorch on 30 golden frames (PCK ≥ 0.9) — else log and mark `parity_ok: false`, do not hard-fail.

- [ ] **Step 4–5:** Run, commit assets under `android/app/src/main/assets/models/` and the script.

## Task 3: Android project scaffold

**Files:** `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/app/build.gradle.kts`, `android/app/src/main/AndroidManifest.xml`, `android/gradle.properties`

- [ ] **Step 1:** Failing check: `cd android && ./gradlew :app:assembleDebug` fails (no project).

- [ ] **Step 3:** Minimal scaffold: `minSdk 26`, `targetSdk 35`, `compileSdk 35`, Kotlin 2.x, deps:
  - `com.google.ai.edge.litert:litert:2.1.0` (pin; 2.2.0 needs verification on device)
  - `androidx.camera:camera-core:1.4.x`, `camera-camera2`, `camera-lifecycle`
  - `org.jetbrains.kotlinx:kotlinx-serialization-json`, `com.squareup.okhttp3:okhttp`
  - abiFilters `arm64-v8a`, `armeabi-v7a`
  - unit-test deps: junit, kotlinx-coroutines-test

- [ ] **Step 4:** `./gradlew :app:assembleDebug :app:testDebugUnitTest` PASS. Add `Makefile` targets `make android-build`, `make android-test`.

- [ ] **Step 5:** Commit.

## Task 4: Kotlin contracts mirror

**Files:** `android/app/src/main/java/com/btb/ondevice/contracts/Contracts.kt`, test `ContractsTest.kt`

- [ ] **Step 1:** Golden test: load `tests/android/fixtures/eating_home.jsonl` and `backend/contracts.py`-produced `FrameEvent`/`AudioEvent`/`RulesLabel`/`EmotionState` samples, serialize/deserialize with `kotlinx.serialization`, assert round-trip equality and exact field names.

```kotlin
@Test fun `parses golden frame event`() {
    val line = fixture("eating_home").first { it.contains("\"kind\":\"features\"") }
    val env = Json.decodeFromString<Envelope>(line)
    val ev = Json.decodeFromJsonElement<FrameEvent>(env.data)
    assertEquals(ev.source, "file")
}
```

- [ ] **Step 3:** Data classes annotated `@Serializable`, `explicitNulls = true`, matching Python field names (`bbox_conf`, `body_keypoints`, `in_feeding_zone`, …) and the fixed emotion enum.

- [ ] **Step 4–5:** Tests green; commit.

## Task 5: LiteRT runtime wrapper

**Files:** `ml/LiteRtRunner.kt`, test with a tiny generated `.tflite` (from `models/` face model) on JVM — use Robolectric-free unit test with `litert` AAR in instrumented test instead; unit-test the *fallback selection logic* with a fake backend.

- [ ] **Step 1:** Test `selectAccelerator(deviceProfile)` returns NPU→GPU→CPU probe order and degrades correctly when NPU init throws.

- [ ] **Step 3:**

```kotlin
class LiteRtRunner(model: MappedByteBuffer, val opts: Options) {
    enum class Accelerator { NPU, GPU, CPU }
    val compiled: CompiledModel = createWithFallback(model, Accelerator.values().toList())
    fun run(input: FloatArray, shape: IntArray): FloatArray
}
```

- [ ] **Step 4–5:** Green; commit. (NCNN variant `NcnnRunner.kt` added only if Task 2 produced NCNN assets.)

## Task 6: Tier-0 gating (motion + VAD)

**Files:** `pipeline/TierZeroGate.kt`, `TierZeroGateTest.kt`

- [ ] **Step 1:** Test: 160×160 grayscale diff energy below threshold → `idle`; audio RMS below `data.audio.silence_rms` → `silent`. Above thresholds → `active`.

- [ ] **Step 3:** Pure Kotlin; no deps. Feed from CameraX Y plane downsampled to 160×160, and AudioRecord buffer.

- [ ] **Step 4–5:** Green; commit.

## Task 7: Dog detector + ByteTrack-lite

**Files:** `vision/DogDetector.kt`, `vision/Tracker.kt`, tests

- [ ] **Step 1:** Golden test: run detector on 8 golden crops, assert bbox IoU ≥ 0.8 vs fixture `det` entries. Tracker test: synthetic 30-frame track with one 5-frame occlusion → ID preserved.

- [ ] **Step 3:** `DogDetector` letterboxes to model input (640 default, config), runs YOLO head decode + class-0 NMS. `Tracker` = Kalman constant-velocity + Hungarian IoU matching + low-score box re-association (from ByteTrack), all pure Kotlin.

- [ ] **Step 4–5:** Green; commit.

## Task 8: Pose estimator + keypoint map

**Files:** `vision/KeypointMap.kt`, `vision/PoseEstimator.kt`, tests

- [ ] **Step 1:** Parity test: for 8 golden crops with known pose input, decoded keypoints within 3 px mean of fixture `keypoints`.

- [ ] **Step 3:** Port `backend/vision/keypoint_map.py` name mapping and `PoseEstimator.estimate(crop)` crop→256×256→model→canonical keypoints. Test uses same crop rects as fixture.

- [ ] **Step 4–5:** Green; commit.

## Task 9: Face landmarks

**Files:** `vision/FaceLandmarker.kt`, test

Parity: landmark arrays within 2 px RMSE of fixture on 8 samples. Reuse `models/dog_face_landmarks_full.tflite` asset.

## Task 10: Audio event detector

**Files:** `audio/AudioEventDetector.kt`, test

Golden parity: same labels as fixture `audio` rows on ≥95% of windows; debounce identical (`data.audio.debounce_s`, `max_gap_s`, `silence_rms` honored). Use `yamnet_classification.tflite` (fixed 15600 samples, 0.975 s), 0.48 s hop ring buffer, label map `bark: [Bark, Bow-wow]` etc., port of `backend/audio/yamnet_events.py`.

## Task 11: Feature extractor port

**Files:** `vision/FeatureExtractor.kt`, test

Golden parity vs fixture `features`: per-feature relative error < 5%, `ear_position` exact match where non-null. Direct port of `backend/vision/features.py` with same window/EMA config from `config.yaml` (embed the `data:` section as `assets/config_data.yaml` or JSON).

## Task 12: Rules engine port

**Files:** `fusion/RulesEngine.kt`, test

Golden parity: replay fixture feature+audio streams through Kotlin `RulesEngine`; label matches fixture `rules` on >95% of frames; confidence within ±0.05. Port `backend/fusion/rules.py` 1:1 (weights, thresholds, hysteresis, treat window).

## Task 13: Fusion state port

**Files:** `fusion/FusionState.kt`, test

State machine: persist ≥3 s before change, no-dog 2 s → unknown, `llm_override_conf` 0.7 passthrough (no LLM on phone in v1), cooldown 60 s, `always_notify` list. Port of `backend/fusion/state.py`; golden replay of fixture rules → same emotions.

## Task 14: Scheduler (staggered cadence + thermal)

**Files:** `pipeline/Scheduler.kt`, test with fake clock

```kotlin
class Scheduler(val cfg: SchedConfig, val clock: () -> Long) {
    fun onFrame(ts: Long): StageSet   // which stages run this tick
    fun onThermal(status: Int)        // drop cadences when hot
}
data class StageSet(val detect: Boolean, val pose: Boolean, val face: Boolean, val audio: Boolean)
```

Cadences: detect every N frames (skip when tracker fresh), pose 1–3 Hz on tracked ROI, face 0.3–1 Hz only when head visible (≥3 head keypoints, per Python `min_head_points`), audio event-gated by TierZeroGate. Thermal: `ThermalManager.getCurrentThermalStatus() >= MODERATE` halves cadences, `>= SEVERE` pose+face only.

## Task 15: Camera/mic capture

**Files:** `capture/CameraXSource.kt`, `capture/MicSource.kt`

CameraX: `ImageAnalysis` YUV_420_888, `STRATEGY_KEEP_ONLY_LATEST`, executor, `rotationDegrees` handling, tight crop → pipeline. Mic: `AudioRecord` 16 kHz mono PCM 100 ms chunks → ring buffer. Target: feeding-zone guide overlay like `/camera` page.

## Task 16: Backend upstream endpoint

**Files:** `backend/main.py`, `backend/web/runtime.py`, tests `tests/web/test_ingest_events.py`

- [ ] **Step 1:** Failing WS test posts envelope lines and asserts `hub.history()` gains the events.

- [ ] **Step 3:** New endpoint:

```python
@app.websocket("/ingest-events")
async def ingest_events(ws: WebSocket):
    await ws.accept()
    runtime = app.state.runtime
    try:
        while True:
            raw = await ws.receive_text()
            env = json.loads(raw)
            t, d = env["type"], env["data"]
            if t == "frame": runtime.on_frame(FrameEvent.model_validate(d))
            elif t == "audio": runtime.on_audio(AudioEvent.model_validate(d))
            elif t == "rules": runtime.on_rules(RulesLabel.model_validate(d))
            elif t == "treat": runtime.treat(d["ts"])
    except WebSocketDisconnect:
        pass
```

- [ ] **Step 4–5:** Tests green; commit. Dashboard path through `Runtime` unchanged.

## Task 17: Event uploader on phone

**Files:** `net/EventUploader.kt`, test with `MockWebServer`

OkHttp WS, JSON-envelope lines `{"type":"frame|audio|rules|treat","data":{...}}`, exponential-backoff reconnect, on-disk spool (last ~5 min) flushed in `ts` order after reconnect. Config: backend `wss://` URL from a single field (no provider hard-coded).

## Task 18: On-device pipeline orchestration

**Files:** `pipeline/OnDevicePipeline.kt`, integration test (JVM, golden fixture replay through all Kotlin ports end-to-end)

```kotlin
class OnDevicePipeline(cfg: AndroidConfig) {
    fun onFrame(ts: Double, yPlane: ByteArray, w: Int, h: Int): FrameBundle
    fun onAudio(ts: Double, pcm16: ByteArray): AudioBundle
    fun status(): Status
}
```

End-to-end golden test: replay `tests/android/fixtures/eating_home.jsonl` Y-plane/audio (exported in Task 1) through all Kotlin ports; assert final EmotionState sequence within tolerance.

## Task 19: Bench + soak

**Files:** `bench/Metrics.kt`, `scripts/android_soak.sh`

Metrics: per-stage latency ms, realized fps per stage, RSS, `ThermalManager` status, battery %; written as JSONL pulled via `adb pull`. 20-min soak on-device replaying a bundled fallback clip; acceptance: sustained fps ≥ targets from CLAUDE.md, no thermal `SEVERE`, memory < 400 MB, battery drop consistent with continuous camera use.

## Task 20: Docs + handoff

**Files:** `docs/ANDROID_ONDEVICE.md` (new), `CLAUDE.md` (Architecture diagram + Commands section updated), `README.md` link

Cover: model export, proxy env for `wss://`, running against `make demo`, metrics interpretation, known-good fallback, when to delete server-side `Pipeline` (after one week stable).

---

## Self-review against the paper's checklist

- Staggered cadence tiers (0–6) → Task 14 ✓
- INT8/FP16 + calibration hard cases → Task 2 ✓
- NNAPI avoided after deprecation → Task 3/5 ✓
- Zero-copy GL→TensorBuffer → Task 15 follow-up slot (optional optimization, gated on CPU-copy profiling) — accepted as post-v1 if profiling says OK
- Per-individual baselines/anomaly → not ported (Python `rules.py` has no baseline logic yet); noted as v1.1
- LLM server-side → Task 16 keeps it possible; phone v1 emits rules only
- Honest limitations (model ≠ behavioral accuracy) → carried into Task 20 docs ✓
