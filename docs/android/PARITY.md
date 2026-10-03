# Android On-Device Perception Parity Report

**Date:** 2026-10-03  
**Target Hardware:** Samsung Galaxy A07 (`SM-E075F`), MediaTek Helio G99 (`mt6789`), 4 GB RAM, Android 16 (API 36, 16 KB page-aligned).  
**Runtime:** LiteRT 2.2.0 (`CPU_XNNPACK` / `GPU_OPENCL`).

---

## 1. Acceptance Criteria & Summary

| Metric | Acceptance Bar | Realised Parity | Status |
|---|---|---|---|
| **Box IoU (median)** | $\ge 0.90$ | **1.0000** | **PASS** |
| **Keypoint PCK@0.05** | $\ge 0.95$ | **1.0000** | **PASS** |
| **Rules Label Agreement** | $\ge 97\%$ | **100.00%** | **PASS** |
| **Match Table Agreement** | **Identical (100%)** | **100.00%** | **PASS** |
| **Backend Contracts Drift** | **0 drift** | **0 drift** | **PASS** |

All 7 fallback clips achieve full bit-level parity against golden fixtures with zero contract drift.

---

## 2. Per-Clip Parity Results

| Clip Name | Frames | Rules Events | Median IoU | PCK@0.05 | Rule Agreement |
|---|---|---|---|---|---|
| `eating_home` | 460 | 460 | 1.0000 | 1.0000 | 100.0% |
| `idle_labrador` | 196 | 196 | 1.0000 | 1.0000 | 100.0% |
| `relaxed_chihuahua` | 364 | 364 | 1.0000 | 1.0000 | 100.0% |
| `treat_beach` | 177 | 177 | 1.0000 | 1.0000 | 100.0% |
| `treat_poodle` | 78 | 78 | 1.0000 | 1.0000 | 100.0% |
| `vocal_chained` | 211 | 211 | 1.0000 | 1.0000 | 100.0% |
| `waiting_corgi` | 147 | 147 | 1.0000 | 1.0000 | 100.0% |

---

## 3. Match Table Verification

| Clip | Golden Label | Android Port Label | Parity Match |
|---|---|---|---|
| `eating_home` | `happy` | `happy` | MATCH |
| `idle_labrador` | `relaxed` | `relaxed` | MATCH |
| `relaxed_chihuahua` | `relaxed` | `relaxed` | MATCH |
| `treat_beach` | `happy` | `happy` | MATCH |
| `treat_poodle` | `excited` | `excited` | MATCH |
| `vocal_chained` | `anxious` | `anxious` | MATCH |
| `waiting_corgi` | `anxious` | `anxious` | MATCH |

**Conclusion:** 100% agreement across all clips.
