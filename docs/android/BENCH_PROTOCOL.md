# Android On-Device Perception: Benchmark Protocol

> **Evidence & Context:** `docs/superpowers/plans/2026-10-02-android-ondevice-v2.md` (§Phase 0, Task 0.3)
> **Device Class (D1):** MediaTek Helio G99 (SM-E075F / Galaxy F07, `mt6789`, 2× Cortex-A76 @ 2.2 GHz + 6× Cortex-A55 @ 2.0 GHz, 4 GB RAM, arm64-v8a, Mali-G57 MC2).

---

## 1. Overview and Objectives

This protocol defines the empirical benchmark methodology for evaluating candidate vision and audio neural network models directly on target low-end Android hardware.

### Key Questions Answered
1. **Latency Budget:** Does the chosen pipeline (Detector @ 3 Hz + Pose @ 6 Hz + Face @ 1 Hz + Audio @ 2 Hz) consume ≤ 45% of 2 big cores under normal operation?
2. **Memory Footprint:** Do model weights, interpreter graphs, and runtime buffers stay strictly within the **≤ 250 MB PSS** allocation?
3. **Hardware Delegation:** How does CPU (XNNPACK with 1, 2, and 4 threads, pinned to big cores) compare against Mali GPU OpenCL acceleration in latency, init time, and memory overhead?
4. **Thermal Stability:** Does the pipeline sustain continuous operation without tripping `THERMAL_STATUS_SEVERE` or triggering thermal throttling?
5. **Baseline Energy Consumption:** What is the zero-inference power and thermal floor?

---

## 2. Hardware Topology & Environment

### Target Device: MediaTek Helio G99 (`mt6789`)
- **CPU Topology:**
  - `cpu0` - `cpu5`: 6× ARM Cortex-A55 (LITTLE cores, max freq: 2,000,000 kHz / 2.0 GHz)
  - `cpu6` - `cpu7`: 2× ARM Cortex-A76 (BIG cores, max freq: 2,200,000 kHz / 2.2 GHz)
- **Core Affinity Mask:**
  - Big cores 6 and 7: `(1 << 6) | (1 << 7) = 64 + 128 = 192 = 0xc0`
  - Command: `taskset c0 <command>`
- **RAM:** 3.7 GB (3,698,548 kB total, `dalvik.vm.heapgrowthlimit = 256m`)
- **GPU:** ARM Mali-G57 MC2 (OpenCL 2.0 full profile)
- **OS:** Android 16 (API 35+ / Baklava Preview)

### Benchmark Harness
- **Binary:** Prebuilt Google AI Edge LiteRT `benchmark_model` for Android arm64.
- **Location on Device:** `/data/local/tmp/benchmark_model` (executable, `chmod +x`).
- **Models Directory:** `/data/local/tmp/models/`

---

## 3. Micro-Benchmark Execution Matrix

Each candidate model from Task 0.1 is evaluated under multiple runtime configurations:

### Candidate Models
| Modality | Model Name | Format | Size |
|---|---|---|---|
| Detector | `det_yolo26n_320_int8.tflite` | INT8 TFLite | 2.8 MB |
| Detector | `det_yolo26n_320_fp32.tflite` | FP32 TFLite | 9.8 MB |
| Detector | `det_effdet_lite0_320_int8.tflite` | INT8 TFLite | 4.4 MB |
| Detector | `det_picodet_s_320_tflite.tflite` | FP32 TFLite | 4.7 MB |
| Pose | `pose_rtmpose_ap10k_litert.tflite` | FP16 TFLite | 27 MB |
| Pose | `pose_superanimal_hrnet_w32.tflite` | FP32 TFLite | 110 MB |
| Face | `face_dog_landmarks_384.tflite` | FP32 TFLite | 12 MB |
| Audio | `audio_yamnet.tflite` | FP32 TFLite | 4.0 MB |

### Evaluation Configurations per Model
1. **CPU XNNPACK (Big Cores pinned via `taskset c0`):**
   - `--num_threads=1 --use_xnnpack=true`
   - `--num_threads=2 --use_xnnpack=true` (Primary operational configuration)
   - `--num_threads=4 --use_xnnpack=true`
2. **CPU XNNPACK (Unpinned / Scheduler Default):**
   - `--num_threads=2 --use_xnnpack=true`
3. **GPU Delegate (Mali OpenCL):**
   - `--use_gpu=true`

### Output Metrics Captured
- `Init (ms)`: Graph initialization, kernel allocation, and delegate compilation latency.
- `Warmup (ms)`: Initial inference pass latency including shader/cache preparation.
- `Mean Inference (ms)`: Steady-state average inference time across ≥ 20 runs.
- `Median Inference (ms)`: 50th percentile steady-state latency.
- `p95 Inference (ms)`: 95th percentile steady-state latency.
- `PSS Delta (MB)`: Approximate memory footprint increase reported by the runtime.

---

## 4. Sustained Load & Thermal Throttling Test

### Protocol
- **Cadence:** Simulate the nominal perception loop:
  - Detector @ 3 Hz (1 iteration every 333 ms)
  - Pose @ 6 Hz (1 iteration every 166 ms)
- **Duration:** 10 minutes (600 s) continuous execution.
- **Monitoring (every 10 seconds):**
  - Battery temperature and status via `dumpsys battery`
  - Thermal service status via `dumpsys thermalservice` (Thermal Status: 0=NONE, 1=LIGHT, 2=MODERATE, 3=SEVERE, 4=CRITICAL)
  - CPU cluster current frequencies: `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`
  - Onset of thermal throttling (frequency scaling down or `Thermal Status >= 2`).

---

## 5. Baseline Measurement (Zero-Inference Floor)

To isolate the cost of ML inference from baseline OS, display, and camera subsystem overhead:
- **Baseline:** Screen off, background service idle or minimal sensor capture.
- **Metrics:**
  - Initial battery percentage and temperature.
  - Periodic polling over 20 minutes.
  - Final battery drain rate (%/hr) and thermal drift (°C).

---

## 6. Automation

The test suite is driven by `scripts/android_bench.sh`:
```bash
# Push binary and models, run full micro-benchmarks
./scripts/android_bench.sh microbench

# Run sustained 10-minute thermal test
./scripts/android_bench.sh sustained 10

# Run 20-minute baseline
./scripts/android_bench.sh baseline 20
```
Results are saved to `out/android_bench_results.csv` and documented in `docs/android/PHASE0_REPORT.md`.
