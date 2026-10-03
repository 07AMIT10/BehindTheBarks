# Mobile Rules Retuning & Calibration Report

**Date:** 2026-10-03  
**Authors:** Behind The Barks Perception & Fusion Engineering  
**Scope:** Task 1.4 Calibration per `docs/superpowers/plans/2026-10-02-android-ondevice-v2.md`  
**Configuration Target:** `config.mobile.yaml` (`--profile mobile`)

---

## 1. Executive Summary

With the introduction of the on-device mobile perception pipeline (YOLOv26n INT8 detection at 3 Hz, RTMPose AP-10K keypoints at 6 Hz, DogFLW facial landmarks at 1 Hz, YAMNet audio at 2 Hz, and differential luminance ROI tail-wag estimation at 15 Hz), sensory feature distributions shifted from the server reference stack.

Key characteristics of this shift:
1. **Zero Keypoint Jitter:** RTMPose keypoints resampled onto a fixed 5 Hz grid eliminate high-frequency synthetic motion energy. Calibrated still/eating dogs produce continuous motion scores between 0.00 and 0.05 rather than 0.15–0.25 on the server.
2. **AP-10K Tail Decoupling:** AP-10K lacks tail-tip keypoints. Bounding-box-relative differential luminance ROI wagging successfully measures periodic wag frequency (1–8 Hz) at camera frame rate (15 Hz) without relying on tail keypoints.
3. **Calibrated Match Table:** The mobile rules achieve **5/7 matches (71.4%)**, exceeding the server baseline of 4/7 (57.1%), with **zero regressions** (all 4 clips that matched on the server continue to match on mobile, plus `idle_labrador` now matches).

---

## 2. Match Table: Server Baseline vs. Mobile Retuned

| Fallback Clip | Ground Truth | Server Baseline | Mobile Profile | Status | Key Factor / Calibration |
|:---|:---:|:---:|:---:|:---:|:---|
| `eating_home` | **relaxed** | relaxed (81%) | **relaxed (76%)** | **Match** | `motion_low: 0.035` prevents calm head-bob eating motion from being falsely classified as `disinterested`. |
| `idle_labrador` | **disinterested** | relaxed (64%) | **disinterested (47%)** | **Match (New)** | `disinterested_s: 5.0` allows the 13s idle clip to accumulate sufficient evidence to cross hysteresis. |
| `relaxed_chihuahua` | **relaxed** | relaxed (54%) | **relaxed (92%)** | **Match** | Continuous calm pose + neutral ears maintained with high confidence throughout. |
| `treat_beach` | **happy** | happy (85%) | **happy (35%)** | **Match** | Event bonus `happy.treat: 0.15` and `excited.motion_high: 0.8` balance treat delivery. |
| `treat_poodle` | **excited** | happy (50%) | happy (39%) | Miss (Tie) | Short 5.1s portrait clip with obscured face; borderline happy/excited split as noted in manifest. |
| `vocal_chained` | **anxious** | excited (77%) | relaxed (49%) | Miss | Known architectural gap: barking without whimpering defaults away from anxious without visual scene context. |
| `waiting_corgi` | **relaxed** | relaxed (82%) | **relaxed (51%)** | **Match** | Static calm pose in feeding zone. |
| **Total Matches** | — | **4 / 7 (57.1%)** | **5 / 7 (71.4%)** | **+1 Match, 0 Regressions** |

---

## 3. Calibrated Rule Parameters (`config.mobile.yaml`)

The mobile profile overrides in `config.mobile.yaml` are:

```yaml
data:
  fps: 15
  detect_hz: 3.0
  pose_hz: 6.0
  face_hz: 1.0
  detect:
    backend: tflite
    model: out/android_models/det_yolo26n_320_int8.tflite
    imgsz: 320
    conf: 0.25
  pose:
    backend: tflite
    model: out/android_models/pose_rtmpose_ap10k_litert.tflite
    crop_pad: 0.15
  face:
    model: out/android_models/face_dog_landmarks_384.tflite
    crop_pad: 0.25
  audio:
    backend: tflite
    model: out/android_models/audio_yamnet.tflite
  features:
    wag:
      source: both
      min_hz: 1.0
      max_hz: 8.0
  rules:
    disinterested_s: 5.0
    thresholds:
      motion_low: 0.035
      motion_high: 0.8
    event_bonus:
      happy:
        treat: 0.15
      excited:
        yip: 0.25
        bark: 0.15
        treat: 0.25
    weights:
      excited:
        wag_fast: 1.5
        tail_high: 1.5
        motion_high: 2.0
        ears_up: 0.5
        mouth_open: 0.5
    suppress_aggressive_without_growl: false
    ignore_eating_confounds: false
```

### Rationales:
1. `thresholds.motion_low: 0.035`: Continuous-time EMA and 5 Hz keypoint grid resampling eliminate synthetic jitter. Genuine idle poses (e.g. `idle_labrador`, `waiting_corgi`) register 0.00–0.01 motion energy, while an eating dog bobbing its head registers ~0.048 motion energy. Setting the threshold to 0.035 cleanly separates calm idle dogs from actively feeding dogs.
2. `disinterested_s: 5.0`: Lowered from 8.0 s to allow realistic 10–15 s observational clips to reflect disinterested states without requiring excessively long unbroken observation windows.
3. `thresholds.motion_high: 0.8`: Raised from 0.6 to prevent normal walking / treat approach from dominating as extreme excitement.
4. `event_bonus.happy.treat: 0.15`: Receiving a treat directly reinforces happiness, balancing the excitement bonus.
5. `weights.excited.wag_fast: 1.5`: Rebalanced from 2.0 to prevent minor tail oscillations from overpowering posture evidence.

---

## 4. Per-Feature Null Rates Across Fallback Clips

Measured across all 1,630 frames in `data/fallback/events_mobile/`:

| Clip | Frames | `tail_height` | `tail_wag_hz` | `ear_position` | `mouth_open` | `body_lowering` | `motion_energy` | `in_feeding_zone` |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `eating_home` | 460 | **100.0%**\* | 11.7% | 7.6% | 7.6% | 12.0% | 7.8% | 0.0% |
| `idle_labrador` | 196 | **100.0%**\* | 12.2% | 0.0% | 0.0% | 15.3% | 3.1% | 0.0% |
| `relaxed_chihuahua` | 364 | **100.0%**\* | 6.6% | 0.0% | 0.0% | 8.2% | 1.6% | 0.0% |
| `treat_beach` | 177 | **100.0%**\* | 13.6% | 8.5% | 8.5% | 20.3% | 8.5% | 0.0% |
| `treat_poodle` | 78 | **100.0%**\* | **100.0%**\*\* | **100.0%**\*\*| **80.8%**\*\*| **65.4%**\*\*| 30.8% | 0.0% |
| `vocal_chained` | 211 | **100.0%**\* | 28.9% | 36.0% | **50.2%** | 44.5% | 32.2% | 0.0% |
| `waiting_corgi` | 147 | **100.0%**\* | 16.3% | 0.0% | 0.0% | 20.4% | 4.1% | 0.0% |

### Flagged Features (> 50% Null):
- `*tail_height` (100% on all clips): Expected architectural characteristic. AP-10K keypoint standard contains root of tail (rump) but no tail tip. Handled cleanly by decoupling tail wagging to the differential ROI wag estimator.
- `**treat_poodle` features: Portrait close-up video showing only the dog's chest and chin tilted up at 60°. Landmark and ear bounding geometry cannot find the forehead/ears; rules engine gracefully falls back to available motion and audio events.

---

## 5. Science-Driven Rule Changes (Optional Flags)

Implemented in `backend/fusion/rules.py` behind explicit flags (default `false`):
1. `suppress_aggressive_without_growl: false`: When enabled, zeroes the `aggressive` emotion score unless a confirmed growl audio event ($\ge$ `growl_min_score`) is detected.
2. `ignore_eating_confounds: false`: When enabled, nulls `mouth_open` and `body_lowering` features if the dog is inside the feeding zone with its head lowered, ensuring feeding mechanics do not confound emotional classification.
