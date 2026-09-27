"""Replay each manifest clip through the live fusion stack and commit the timelines.

    .venv/bin/python scripts/precompute_demo.py [--manifest PATH] [--clip ID]

Run while online with .env configured for the LLM you want to demo. Without LLM config it still
writes a rules-only timeline (llm: []). Outputs: backend/demo/cache/<id>.timeline.json (committed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend.demo.clips import load_demo_clips  # noqa: E402
from backend.demo.replay import replay_clip  # noqa: E402
from backend.fusion.llm_interpreter import LLMInterpreter  # noqa: E402
from backend.web.settings import LLMSettings, load_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--clip", default=None)
    a = ap.parse_args(argv)
    cfg = load_config()
    if a.manifest:
        cfg["web"]["demo"]["manifest"] = a.manifest
    clips = load_demo_clips(cfg)
    if not clips.available:
        sys.exit("no demo clips found (check web.demo.manifest and backend/demo/clips/manifest.json)")
    cache = Path("backend/demo/cache")
    cache.mkdir(parents=True, exist_ok=True)
    settings = LLMSettings.from_config(cfg)
    print("LLM:", "enabled" if settings.enabled else "disabled (rules-only timelines)")
    for meta in clips.list():
        if a.clip and meta.id != a.clip:
            continue
        video = clips.video_path(meta.id)
        cap = cv2.VideoCapture(str(video))

        def get_frame(t: float, cap=cap):
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok:
                return None
            ok2, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            return buf.tobytes() if ok2 else None

        tl = asyncio.run(replay_clip(clips.events(meta.id), get_frame, LLMInterpreter(settings)))
        tl["clip"] = meta.id
        (cache / f"{meta.id}.timeline.json").write_text(json.dumps(tl))
        print(f"{meta.id}: {len(tl['states'])} states, {len(tl['llm'])} llm, {len(tl['notifications'])} notes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
