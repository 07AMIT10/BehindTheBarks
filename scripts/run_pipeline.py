"""Run the full Data pipeline on a clip, the webcam, a stream or a simulated phone.

    python scripts/run_pipeline.py --source file --path data/fallback/raw/german_shepherd_treat.webm --fast \\
        --jsonl out/events.jsonl --debug-video out/debug.mp4 --treat-at 6
    python scripts/run_pipeline.py --source browser --path clip.mp4 --portrait --jsonl out/phone.jsonl
    python scripts/run_pipeline.py --source webcam --duration 30

`--source browser` drives the pipeline in-process through scripts/fake_phone.py: the clip is the phone,
and its frames and audio go in through Pipeline.ingest_frame / ingest_audio, exactly as Web's /ingest will.

--jsonl writes every event as one line: {"type": "frame" | "audio" | "rules" | "treat", "data": {...}}
(the same envelope as Web's WebSocket), in timestamp order when --fast is used.
--treat-at SECONDS (repeatable) presses the "treat dropped" button that many seconds into the clip.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.contracts import EMOTIONS, AudioEvent, FrameEvent, RulesLabel  # noqa: E402

COLORS = {
    "happy": (80, 200, 80), "excited": (0, 165, 255), "relaxed": (200, 200, 80), "anxious": (0, 200, 220),
    "fearful": (200, 80, 200), "aggressive": (60, 60, 230), "disinterested": (160, 160, 160), "unknown": (110, 110, 110),
}


def draw_overlay(frame, ev: FrameEvent, label: RulesLabel, audio: list[AudioEvent], zone, treat_active: bool):
    """Skeleton, face points, feeding zone, bbox, and the rules label with all its scores."""
    from backend.vision.face import draw_face
    from backend.vision.pose import draw_skeleton

    out = frame.copy()
    h, w = out.shape[:2]
    k = max(0.5, min(w, h) / 600)  # text scale that also works on small or portrait frames
    in_zone = bool(ev.features.in_feeding_zone)
    cv2.polylines(out, [np.array([[x * w, y * h] for x, y in zone], np.int32)], True, (0, 200, 0) if in_zone else (0, 0, 220), 2)
    if ev.bbox is not None:
        cv2.rectangle(out, tuple(int(v) for v in ev.bbox[:2]), tuple(int(v) for v in ev.bbox[2:]), (255, 160, 0), 2)
    if ev.body_keypoints:
        out = draw_skeleton(out, {n: kp for n, kp in ev.body_keypoints.items()})
    if ev.face_landmarks:
        out = draw_face(out, [list(p) for p in ev.face_landmarks], None)

    x0, y, line = 10, int(30 * k), int(24 * k)
    cv2.putText(out, f"{label.emotion} {label.confidence:.2f}", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9 * k,
                COLORS[label.emotion], max(2, int(2 * k)), cv2.LINE_AA)
    for emotion in EMOTIONS:
        y += line
        s = label.scores[emotion]
        cv2.rectangle(out, (x0, y - int(12 * k)), (x0 + int(120 * k * s), y), COLORS[emotion], -1)
        cv2.putText(out, f"{emotion} {s:.2f}", (x0 + int(130 * k), y), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * k,
                    (255, 255, 255), 1, cv2.LINE_AA)
    y += line
    heard = ", ".join(f"{a.label} {a.score:.2f}" for a in audio if a.ts > ev.ts - 3.0) or "-"
    cv2.putText(out, f"audio: {heard}", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * k, (0, 255, 255), 1, cv2.LINE_AA)
    if treat_active:
        y += line
        cv2.putText(out, "TREAT", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * k, (0, 165, 255), max(2, int(2 * k)), cv2.LINE_AA)
    return out


class _PhoneSink:
    """What the phone talks to: the Pipeline's ingest methods."""

    def __init__(self, pipeline):
        self.push_frame, self.push_audio = pipeline.ingest_frame, pipeline.ingest_audio


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--source", choices=["file", "webcam", "stream", "browser"], help="default: data.source.type")
    ap.add_argument("--path", help="video file / stream URL; for --source browser, the clip that plays the phone")
    ap.add_argument("--audio-override", help='audio file, or "mic"')
    ap.add_argument("--device", type=int, help="webcam index")
    ap.add_argument("--fps", type=float, help="pipeline fps (data.fps)")
    ap.add_argument("--fast", action="store_true", help="file only: batch speed, deterministic, nothing dropped")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    ap.add_argument("--treat-at", type=float, action="append", default=[], metavar="S", help="treat dropped S seconds into the clip")
    ap.add_argument("--jsonl", help="write every event here, one JSON per line")
    ap.add_argument("--debug-video", help="write an annotated mp4 here")
    ap.add_argument("--portrait", action="store_true", help="browser: the fake phone rotates frames 90 degrees")
    ap.add_argument("--phone-fps", type=float, default=10.0, help="browser: frames per second the fake phone sends")
    return ap.parse_args(argv)


async def amain(args) -> dict:
    import yaml

    from backend.pipeline import Pipeline

    cfg = yaml.safe_load(Path(args.config).read_text())
    cfg = copy.deepcopy(cfg)
    src = cfg["data"].setdefault("source", {})
    if args.source:
        src["type"] = args.source
    for key, val in (("path", args.path if args.source != "browser" else None), ("audio", args.audio_override),
                     ("device", args.device)):
        if val is not None:
            src[key] = val
    if args.loop:
        src["loop"] = True
    if args.fast:
        if src.get("type") != "file":
            logging.warning("--fast only applies to file sources; ignoring")
        else:
            src["fast"] = True
    if args.fps:
        cfg["data"]["fps"] = args.fps
    is_browser = src.get("type") == "browser"
    if is_browser and not args.path:
        raise SystemExit("--source browser needs --path CLIP (the video that plays the phone)")

    out_fh = None
    if args.jsonl:
        Path(args.jsonl).parent.mkdir(parents=True, exist_ok=True)
        out_fh = open(args.jsonl, "w")
    writer = None
    zone = cfg["data"]["feeding_zone"]
    treat_window = float(cfg["data"].get("rules", {}).get("treat_window_s", 10.0))
    treats: list[float] = []  # absolute ts, sorted
    counts = {"frame": 0, "audio": 0, "rules": 0, "treat": 0}

    def write(kind: str, data: dict) -> None:
        counts[kind] += 1
        if out_fh:
            out_fh.write(json.dumps({"type": kind, "data": data}) + "\n")

    written_treats = 0

    def flush_treats(upto: float) -> None:
        nonlocal written_treats
        while written_treats < len(treats) and treats[written_treats] <= upto:
            write("treat", {"ts": treats[written_treats]})
            written_treats += 1

    def tap(frame, ev, label, audio) -> None:
        nonlocal writer
        from backend.vision.videoio import DebugVideoWriter

        if writer is None:
            writer = DebugVideoWriter(args.debug_video, cfg["data"].get("fps", 8))
        active = any(t <= ev.ts <= t + treat_window for t in treats)
        writer.write(draw_overlay(frame, ev, label, audio, zone, active))

    if args.debug_video:
        Path(args.debug_video).parent.mkdir(parents=True, exist_ok=True)
    pipeline = Pipeline(cfg, frame_tap=tap if args.debug_video else None)

    if args.treat_at:
        if is_browser or src.get("type") != "file":
            # Live sources have no clip clock; the treat is placed relative to when we start.
            base = time.time()
        else:
            base = pipeline.clock_start()
        for s in sorted(args.treat_at):
            treats.append(base + s)
            pipeline.mark_treat(base + s)

    def on_frame(ev: FrameEvent) -> None:
        flush_treats(ev.ts)
        write("frame", json.loads(ev.to_json()))

    def on_audio(ev: AudioEvent) -> None:
        write("audio", json.loads(ev.to_json()))

    def on_rules(label: RulesLabel) -> None:
        write("rules", json.loads(label.to_json()))

    loop = asyncio.get_running_loop()
    phone = None
    if is_browser:
        from scripts.fake_phone import FakePhone

        phone = FakePhone(_PhoneSink(pipeline), args.path, args.audio_override, fps=args.phone_fps,
                          portrait=args.portrait, loop=args.loop, duration=args.duration)

    task = asyncio.create_task(pipeline.run(on_frame, on_audio, on_rules))
    if phone is not None:
        phone.start()

        def end_with_phone() -> None:
            phone.wait()
            time.sleep(2.0)  # let the last frames and audio windows drain
            pipeline.stop()

        threading.Thread(target=end_with_phone, name="phone-watch", daemon=True).start()
    elif args.duration:
        loop.call_later(args.duration, lambda: loop.run_in_executor(None, pipeline.stop))

    try:
        await task
    except asyncio.CancelledError:
        pipeline.stop()
    finally:
        if phone is not None:
            phone.stop()
        pipeline.stop()
        flush_treats(float("inf"))
        if writer is not None:
            writer.release()
        if out_fh:
            out_fh.close()
    return {"counts": counts, "status": pipeline.status()}


def main(argv=None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    result = asyncio.run(amain(args))
    c = result["counts"]
    print(f"frames={c['frame']} rules={c['rules']} audio={c['audio']} treats={c['treat']}"
          + (f"  -> {args.jsonl}" if args.jsonl else "") + (f"  -> {args.debug_video}" if args.debug_video else ""))


if __name__ == "__main__":
    main()
