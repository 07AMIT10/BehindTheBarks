# Evidence brief: on-device dog perception on a ~3 GB Android phone

Companion to `docs/superpowers/plans/2026-10-02-android-ondevice-v2.md`. Compiled 2026-10-02 from four
research passes (runtime/platform, models, affective science, repo audit) plus the pasted Sci-Hub
"Building a Real-Time Animal Behavior Monitor" report.

Labels: **[DOC]** official docs or release notes · **[BENCH]** published measurement (device named) ·
**[PAPER]** peer-reviewed · **[INF]** our inference, must be measured · **[U]** could not verify.

---

## 1. What the pasted report gets right, wrong, or doesn't cover

| Claim in the report | Verdict |
|---|---|
| Use a tiered cascade at staggered cadences, gated by a cheap motion/audio tier | **Keep.** Matches the platform evidence. |
| Pose at 1–3 Hz is enough | **Wrong for us.** `features.py` wag needs ≥10 tail samples in 3 s and a 1.5 s run (≈3.3–6.7 Hz). `motion_energy` and the EMAs are tuned at 4.7–8 fps. See §4. |
| EAPoseNet (12.3 M params, 2.5 GFLOPs, SD865 speed) | AP-vs-RTMPose-M checks out [PAPER]. GFLOPs and SD865 speed are [U] (paywalled). **No public code or weights, so it is not usable.** |
| Steagall 2023: 95.5 % FGS from landmarks + XGBoost | The number is correct, but it is binary pain/no-pain, on *annotated* landmarks, with an image-level split. It is not an end-to-end deployable result, and it is not about emotion. |
| Phelipon 2025: horse emotion 87 % | The task is binary comfortable vs uncomfortable, labelled with pain scales, with a random non-horse-exclusive split. Calling it "emotion" overstates it. |
| Gómez-Armenta 2024: 19,643 barks / 113 dogs, LLD features best | Correct. The tasks are identity, breed, sex and *context*, not emotion, and performance drops when individuals are held out. |
| Broomé 2023 survey recommendations | Correct (imbalance, leave-one-animal-out, cross-domain bias). |
| ShaderNN / EdgeNN energy numbers | Real papers, but they cover flagship or mid-range GPUs and custom engines. They don't apply to a Mali-G52 phone running stock runtimes. |
| "Use NNAPI/QNN delegates" | **Outdated.** NNAPI is deprecated in Android 15 [DOC]. The LiteRT NPU path supports only flagship Qualcomm, Dimensity 7300+, Tensor and Exynos, with **no Helio G, Unisoc, SD4-series or SD680** [DOC]. |
| INT8 is risky (PTQ study) | Directionally right. Specifics in §3. |
| Not covered at all | Model **licensing** (§6), Android **foreground-service** rules, **16 KB pages**, **clock skew** phone→server, and the fact that our rules are **tuned to HRNet-W32's noise profile**. |

## 2. Runtime and platform (low-end SoC: Helio G85/G88/G99, SD680, SD4 Gen 1/2, Unisoc T6xx)

- **LiteRT 2.2.0** (2026-08-14) [DOC]. CompiledModel is the current API; Interpreter is kept for
  compatibility.
  - On LiteRT Maven **V2, GPU inference requires CompiledModel**; Interpreter is CPU-only [DOC].
  - minSdk 23. ARMv7 prebuilts since 2.1.5 [DOC].
  - The GPU backend on Android is OpenCL/OpenGL. There is **no official GPU compatibility list** [DOC]. There are open correctness issues on the OpenCL path (#10192) [DOC].
- **No NPU on our tier.** Plan CPU-first.
- **GPU vs CPU on low-end.**
  - ncnn's table puts Mali-G52 Vulkan at about one A55 thread for MobileNet- and NanoDet-class models, slower than 4×A55 [BENCH, RK3566/RK3568].
  - MDLBench on a Redmi 9 (Helio G80): *"On INT8-based models, GPU can hardly bring any benefit."* Cold start is 1.3–45× slower than warm [BENCH].
  - Use the GPU, if at all, to **offload one FP16 model** so CPU cores stay free [INF].
- **ONNX Runtime 1.30** [DOC]. XNNPACK EP: set `intra_op_num_threads=1`, size the XNNPACK pool to physical cores, turn spinning off. 16 KB-aligned since 1.25 [DOC].
- **ncnn 20260526** [DOC]. 16 KB-aligned since 20241226. Best published low-end numbers.
- **ExecuTorch 1.x GA** [DOC]. Vulkan quantized conv is "future", so not a fit for INT8 CNNs today.
- **Foreground service** [DOC]:
  - Declare `foregroundServiceType="camera|microphone"` and hold `FOREGROUND_SERVICE_CAMERA` / `_MICROPHONE`.
  - Both are while-in-use types: start the service from a visible Activity.
  - **No timeout** for camera or microphone (the Android 15 limits only hit dataSync and mediaProcessing).
  - Screen-off capture works with ImageAnalysis-only bound to the service lifecycle plus a partial wakelock [INF/community]. OEM killers (MIUI, Transsion) need battery-optimisation exemption [INF].
- **Thermal** [DOC]:
  - `addThermalStatusListener` (API 29).
  - `getThermalHeadroom(forecast)` (API 30): poll no more than every 10 s or it returns NaN. Suggested thresholds: >0.85 watch, >0.95 reduce.
  - Some devices always report NONE or NaN.
  - `PerformanceHintManager` (API 31). `setPreferPowerEfficiency` (API 35). CPU/GPU headroom in `SystemHealthManager` (API 36).
- **Memory** [DOC]:
  - mmap'd weights are clean and reclaimable.
  - XNNPACK repacking and GPU delegates create **anonymous** copies, which count against us [INF].
  - Read `getMemoryClass()` on the device.
  - Since Android 14, only `TRIM_MEMORY_UI_HIDDEN` and `TRIM_MEMORY_BACKGROUND` are delivered.
- **16 KB pages** [DOC]: enforced on Play from **2027-02-01** for targetSdk 35+. AGP ≥ 8.5.1 and NDK ≥ r28. LiteRT ≥ 1.4, ncnn ≥ 20241226 and ORT ≥ 1.25 are aligned.
- **CameraX** [DOC]:
  - Use `STRATEGY_KEEP_ONLY_LATEST` and `OUTPUT_IMAGE_FORMAT_RGBA_8888` (CameraX converts internally).
  - Cap fps with `CONTROL_AE_TARGET_FPS_RANGE` after checking the available ranges; CameraX 1.5 adds `setExpectedFrameRateRange`.
  - Also throttle in the analyzer, because some HALs ignore the range [INF].
- **Power.** The camera pipeline alone draws about 1 W (2015 measurement, dated) [BENCH]. On-device power rails exist only on Pixel 6+ [DOC]. On a low-end target, use batterystats plus an external USB meter.
- **VAD.** Silero and WebRTC VAD are speech-tuned and likely miss barks [INF]. Use an RMS or spectral-flux onset gate instead.
- **Multi-DNN schedulers** (ADMS, Puzzle, CARIn, 2024–25) [BENCH] all target NPU or flagship heterogeneity. On CPU-only SoCs, the usable tools are cascade gating, thermal-driven rate ladders, big-core pinning, and **serial** (never concurrent) model execution [INF].

## 3. Models

### Detection (COCO "dog" = class 16)

| Model | Input | mAP | Params / FLOPs | Phone evidence | Licence |
|---|---|---|---|---|---|
| YOLO26n (Jan 2026, NMS-free, no DFL) | 640 | 40.9 (e2e 40.1) | 2.4 M / 5.5 G | SD 8 Elite Gen 5: 52 ms CPU w8a32, 16 ms GPU [BENCH]. **No low-end data** | **AGPL-3.0** (weights too) |
| YOLO11n | 640 | 39.5 | 2.6 M / 6.5 G | none low-end | AGPL-3.0 |
| MediaPipe EfficientDet-Lite0 | 320 | 26.4 fp / 26.1 int8 | – | Pixel 6: 29 ms CPU int8 [BENCH] | Apache-2.0 code; model terms not stated [U] |
| PP-PicoDet-S | 320 | 27.1 | 0.99 M / 0.73 G | 8 ms ncnn on SD865-class [BENCH] | Apache-2.0 |
| NanoDet-Plus-m | 320 | 27.0 | 1.17 M / 0.9 G | 12 ms ncnn, Kirin 980 [BENCH]; 53 ms on 4×A55 (nanodet_m) [BENCH] | Apache-2.0 |

- Full-INT8 YOLO TFLite loses about 15 % relative mAP (YOLOv8s: 0.449 → 0.382, Ultralytics #9473) [BENCH].
- Ultralytics' own phone benchmarks use w8a32 or w8a16, not full INT8.
- No published per-class "dog" AP at 320 [U]. Our dog is large in a fixed frame, so we **measure on the fallback clips** instead.

### Body pose

- **DLC SuperAnimal-Quadruped HRNet-W32 (current).**
  - About 28 M params. 92 ms/frame on an M2 (MPS).
  - **Licence is academic and non-commercial** [DOC].
  - No official TFLite or ONNX export.
  - **Not viable at ≥5 Hz on a low-end CPU** [INF].
  - An `rtmpose_s` SuperAnimal variant is [U].
- **RTMPose (mmpose, Apache-2.0).**
  - Body-model sizes: t = 3.3 M / 0.36 G, s = 5.5 M / 0.68 G, m = 13.6 M / 1.93 G.
  - SD865 ncnn FP16: t 9 ms, s 14 ms, m 26 ms [BENCH].
  - **AP-10K: only RTMPose-m is published** (72.2 AP at 256², 2.57 G). AP-10K has **17 keypoints with one "tail" point and no tail tip or ear tips**.
- **Dog keypoints with tail_tip and ears.**
  - StanfordExtra annotations: 20 kp including tail and ears, **MIT since Nov 2024**. Images are Stanford Dogs/ImageNet with research terms [U].
  - Ultralytics Dog-Pose: 24 kp, nearly our canonical 19. Training with Ultralytics makes the weights AGPL.
- **INT8 pose.** mmpose/mmdeploy issues show accuracy drops even at TensorRT FP16 (#2579, #2913). Plan **FP16 or dynamic-range**, with the SimCC head kept at higher precision [INF].
- **Tail tip is unreliable on generic models.** A 2026 tail-kinematics study trained per-dog DLC at 30 fps [PAPER, bioRxiv].

### Face landmarks

- **hugocornellier/dog-face-landmarks** [DOC]:
  - MobileNetV3-L at 384 px, fp16 static TFLite, 11 MB, NME_IOD 8.56.
  - 27 ms CPU / 3.8 ms GPU on an **M4 Max**, so expect several times slower on low-end.
  - **Weights CC BY-NC 4.0.**
  - We already ship this model. The repo downloads the 224 px localizer but **never uses it** (the head box comes from pose).
- **DogFLW** [PAPER]: ear error is highest, worst for floppy and long-haired breeds. Landmarks failed on 18 % of frames.
  - In a 2025 follow-up, more than half of the videos were excluded because fewer than 50 % of their frames got landmarks.
  - **Plan for frequent dropouts.**

### Audio

- **YAMNet** [BENCH]:
  - 3.7 M params, 69 M mult/frame.
  - MediaPipe's float32 `yamnet.tflite`, with a fixed 15600-sample input, runs **12.3 ms CPU on a Pixel 6**.
  - Apache-2.0.
  - Don't re-quantize it: the STFT is in-graph [INF].
  - The quantized TF-Hub "classification" variant is recorded in agent-memory but was [U] this pass. Either file fits our 15600-sample windowing.
- **EfficientAT** mn04/mn05 [DOC]: MIT licence, AudioSet mAP 43–44 vs YAMNet 31 at similar or lower compute. PyTorch only, conversion needed. A v1.1 option.
- **No validated mobile dog-emotion-from-audio model exists.**
  - Abzaliev 2024: 62 % vs a 56 % majority baseline for 4-class context [PAPER].
  - Molnár 2008: 43 % for 6 contexts [PAPER].
  - Keep acoustic event labels only.

### Temporal, tracking, tail wag

- **Single dog, static camera:** one constant-velocity Kalman filter on the box. No ReID or ByteTrack [INF].
- **Temporal model:** a GRU/TCN over about 3 s of features (well under 0.1 M params, under 1 ms) is enough. No dog-emotion temporal dataset is large enough to train on [PAPER survey].
- **Wag definition** used in recent ethology: tail-angle oscillation ≥ 20° peak-to-peak and ≥ 1 Hz, FFT over 2 s, measured at 30 fps. That study found **no left/right asymmetry effect** [PAPER, bioRxiv 2026].
- **Wag from optical flow:** no paper found for frame-difference or optical-flow wag-Hz estimation [U]. We'd be the ones validating it.

## 4. Repo facts that drive the design (audit of `backend/`)

- **Measured on M2 with a real dog clip:** 4.7 fps mean. Mean ms per frame: detect 45, pose 92, face 92, total 232. 59 % of 5 s windows were under 5 fps (`docs/DATA_HANDOFF.md:162-178`).
- **Rules are tuned to the HRNet-W32 noise profile.** Swapping to resnet_50 dropped the fallback match from 5/7 to 3/7 (`config.yaml:31-34`).
  - **Any on-device pose model therefore means retuning.** Pixel-parity against HRNet is the wrong acceptance test.
  - Today's match is **4/7**: idle_labrador, treat_poodle (near-tie) and vocal_chained miss.
- **Frame-rate dependence inside features:**
  - `_Ema` uses a per-sample alpha (`features.py:97-111`).
  - Detector EMA and miss-reset count frames (`detect.py:133-154`).
  - `motion_energy` = pose jitter / dt, so it grows with fps (`features.py:388-410`).
  - Wag needs ≥ 3.3–6.7 Hz of tail-visible samples. At 4.7 fps `wag_fast` (3 Hz ± 1) is effectively unreachable (Nyquist ≈ 2.35 Hz).
- **Face is single-stage.** Head box from pose keypoints, 384² input, 92 outputs, no confidence output. Plausibility checks reject bad outputs (`face.py:141-214`). The TFLite CPU thread table is 1→263 ms, 2→135, 4→71 (M2).
- **YAMNet in Python** is the full TF-Hub SavedModel, not TFLite. The windowing (15600 samples, 0.48 s hop, max-pool) already matches the TFLite variant.
- **`rules.py` and `state.py` are pure Python** (about 360 + 160 lines) and port to Kotlin 1:1. Watch two things:
  - `round(…, 4)`.
  - `max()` ties resolved by dict order: happy, excited, relaxed, anxious, fearful, aggressive, disinterested, unknown.
- **Backend:** events enter only via `Runtime.on_frame/on_audio/on_rules`; the Runtime is `app.state.rt`.
  - The real Pipeline's watchdog emits no-dog frames when the browser source is stalled. **A phone-events mode must not run the real Pipeline.**
  - `/video`, LLM and notification snapshots use `pipeline.latest_frame_jpeg()`, so the phone must also supply preview JPEGs.

## 5. Affective science: what the product can honestly claim

Full citation list is in the research transcript. Key points:

- **Arousal vs valence.** Wag rate, motion, mouth open and barking signal **arousal, not valence**.
  - Leonetti 2024 (Biol Lett), Bremhorst 2019/2022, Caeiro 2017.
  - Bremhorst 2022: *"none [of the facial expressions] would allow consistent correct classifications"* when used individually.
- **Feeding-zone confounds.** Lip-licking, body lowering and mouth open are all normal during eating, so they can't be read as stress at the bowl.
- **Best-supported contrast.** Ear position, from the **food-anticipation vs blocked-access** paradigm (Bremhorst 2019, 29 Labradors; replicated 2022).
  - Our treat button is exactly that context marker.
- **LLMs on dog emotion** (Martvel et al. 2025, Sci Rep):
  - About 60–65 % on web images with lay labels.
  - **Near chance** on experimentally induced states.
  - Labels change when only the background is swapped.
  - **The current `llm_override_conf: 0.7` (the LLM can override rules) is not evidence-backed.**
- **"Aggressive" / resource guarding** needs a social trigger: a person or dog approaching. With the dog alone at the bowl it should be **suppressed**.
- **"Disinterested"** is better framed as an **appetite flag**: inappetence is a non-specific clinical sign (Johnson & Freeman 2017, JAVMA).
- **Baselines** (per dog, by time of day, EWMA/CUSUM on daily aggregates) are the strongest-evidenced long-term value.
  - Livestock: Quimby 2001, CUSUM on bunk time flagged disease about 4 days early.
  - Wagner 2020: 66 % false positives, so alarms need prioritisation.
- **In-the-wild yield is low.** Martvel & Riemer 2025 could use only 11 of 42 owner videos. Report per-cue visibility rates.
- **Feasible validation:**
  - 5–10 dogs, owner-logged context.
  - Two blind raters on a fixed ethogram, reporting κ or α.
  - Per-cue sensitivity and specificity by visibility.
  - Leave-one-dog-out, per-breed and per-ear-type breakdowns.

## 6. Licensing: blocks distribution, not the demo

| Component | Licence | Effect on a distributed APK |
|---|---|---|
| Ultralytics YOLO11/26 (code **and** weights, including ones we train) | AGPL-3.0 / Enterprise | Release the whole app's source under AGPL, or buy Enterprise |
| DLC SuperAnimal weights | academic, non-commercial | No commercial use without an EPFL licence |
| DogFLW dataset + hugocornellier weights | CC BY-NC 4.0 | No commercial use |
| YAMNet | Apache-2.0 | OK |
| RTMPose / mmpose, PicoDet, NanoDet, D-FINE, RF-DETR | Apache-2.0 | OK |
| StanfordExtra annotations | MIT (images: research terms [U]) | Probably OK for fine-tune weights; get legal review |
| AP-10K / APT-36K | CC-BY-4.0 | OK |

The repo itself is "all rights reserved" (README:199), so embedding AGPL YOLO in a closed APK conflicts
with it today.
