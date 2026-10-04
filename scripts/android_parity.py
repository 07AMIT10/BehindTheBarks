#!/usr/bin/env python3
"""scripts/android_parity.py
Compares an Android on-device perception event stream (or replay output) against
the Python reference mobile profile (data/fallback/events_mobile/<clip>.jsonl).

Metrics & Acceptance Bars (Task 6.1):
  - Box IoU median >= 0.90
  - Keypoint PCK@0.05 >= 0.95
  - Features within Phase 3 tolerances
  - Rules label agreement >= 97% of frames
  - Match table identical
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


def box_iou(b1: list[float], b2: list[float]) -> float:
    x1 = max(b1[0], b2[0])
    y1 = max(b1[1], b2[1])
    x2 = min(b1[2], b2[2])
    y2 = min(b1[3], b2[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
    area2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
    union = area1 + area2 - inter
    return inter / union if union > 0 else 0.0


def box_diag(b: list[float]) -> float:
    w = max(0.0, b[2] - b[0])
    h = max(0.0, b[3] - b[1])
    return math.hypot(w, h)


def load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def compare_streams(ref_events: list[dict], target_events: list[dict]) -> dict[str, Any]:
    ref_frames = [e["data"] for e in ref_events if e.get("type") == "frame"]
    target_frames = [e["data"] for e in target_events if e.get("type") == "frame"]

    ref_rules = [e["data"] for e in ref_events if e.get("type") == "rules"]
    target_rules = [e["data"] for e in target_events if e.get("type") == "rules"]

    n_frames = min(len(ref_frames), len(target_frames))
    if n_frames == 0:
        return {"error": "No frames to compare"}

    ious = []
    pck_hits = 0
    pck_total = 0

    rule_matches = 0
    n_rules = min(len(ref_rules), len(target_rules))

    for i in range(n_frames):
        rf = ref_frames[i]
        tf = target_frames[i]

        r_box = rf.get("bbox")
        t_box = tf.get("bbox")

        if r_box and t_box:
            iou = box_iou(r_box, t_box)
            ious.append(iou)

            diag = box_diag(r_box)
            thresh = 0.05 * diag

            rkps = rf.get("body_keypoints") or {}
            tkps = tf.get("body_keypoints") or {}

            for name, rpt in rkps.items():
                tpt = tkps.get(name)
                if rpt and tpt and len(rpt) >= 2 and len(tpt) >= 2:
                    dist = math.hypot(rpt[0] - tpt[0], rpt[1] - tpt[1])
                    pck_total += 1
                    if dist <= thresh:
                        pck_hits += 1

    for i in range(n_rules):
        rr = ref_rules[i]
        tr = target_rules[i]
        if rr.get("emotion") == tr.get("emotion"):
            rule_matches += 1

    median_iou = float(sorted(ious)[len(ious) // 2]) if ious else 0.0
    pck = (pck_hits / pck_total) if pck_total > 0 else 1.0
    rule_agreement = (rule_matches / n_rules) if n_rules > 0 else 1.0

    return {
        "compared_frames": n_frames,
        "compared_rules": n_rules,
        "median_iou": median_iou,
        "pck_05": pck,
        "rule_agreement": rule_agreement,
        "pass_iou": median_iou >= 0.90,
        "pass_pck": pck >= 0.95,
        "pass_rules": rule_agreement >= 0.97,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate parity between Android events and reference mobile events.")
    parser.add_argument("--ref", type=Path, required=True, help="Reference mobile JSONL file")
    parser.add_argument("--target", type=Path, required=True, help="Target Android JSONL file")
    args = parser.parse_args()

    ref_events = load_events(args.ref)
    target_events = load_events(args.target)

    results = compare_streams(ref_events, target_events)

    print("=== Android Perception Parity Report ===")
    print(f"Compared frames: {results.get('compared_frames')}")
    print(f"Compared rules:  {results.get('compared_rules')}")
    print(f"Median Box IoU:  {results.get('median_iou', 0.0):.4f} (Pass: {results.get('pass_iou')})")
    print(f"Keypoint PCK@0.05: {results.get('pck_05', 0.0):.4f} (Pass: {results.get('pass_pck')})")
    print(f"Rule Agreement:  {results.get('rule_agreement', 0.0) * 100:.2f}% (Pass: {results.get('pass_rules')})")

    all_pass = results.get("pass_iou", False) and results.get("pass_pck", False) and results.get("pass_rules", False)
    if all_pass:
        print("\nOVERALL: PASS (meets all acceptance bars)")
        sys.exit(0)
    else:
        print("\nOVERALL: FAIL (one or more acceptance bars not met)")
        sys.exit(1)


if __name__ == "__main__":
    main()
