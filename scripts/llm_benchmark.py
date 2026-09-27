"""Benchmark the configured LLM provider over N saved frames.

    .venv/bin/python scripts/llm_benchmark.py --frames out/bench-frames [--n 20] [--rules out/bench-rules.jsonl]

Run once with Groq env and once with OpenRouter env; recommend the default from median/p95 latency,
parse-failure rate and rules agreement. Frames: extract with
`ffmpeg -i clip.mp4 -vf fps=1 out/bench-frames/f%03d.jpg` (imageio-ffmpeg binary works too).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend.contracts import Features  # noqa: E402
from backend.fusion.llm_interpreter import LLMInterpreter  # noqa: E402
from backend.web.settings import LLMSettings, load_config  # noqa: E402


async def one(it: LLMInterpreter, jpeg: bytes, now: float, rules) -> dict:
    feats = [(now - 1, Features(tail_height=0.4, tail_wag_hz=2.0, ear_position="neutral", mouth_open=0.5,
                                body_lowering=0.1, motion_energy=0.4, in_feeding_zone=True))]
    r = await it.interpret(trigger="benchmark", now=now, features=feats, audio=[], rules=rules, jpeg=jpeg)
    last = it.last_call or {}
    agree = r is not None and rules is not None and r.emotion == rules.emotion
    return {"ok": r is not None, "latency_ms": last.get("latency_ms"), "agree": agree}


async def amain(paths: list[Path], rules: list) -> list[dict]:
    load_dotenv()
    s = LLMSettings.from_config(load_config())
    if not s.enabled:
        sys.exit("LLM not configured (.env)")
    it = LLMInterpreter(s)
    out = []
    for i, p in enumerate(paths):
        out.append(await one(it, p.read_bytes(), time.time(), rules[i] if i < len(rules) else None))
        print(f"{i + 1}/{len(paths)}: {out[-1]}", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--rules", default=None, help="jsonl of RulesLabel, one per frame")
    a = ap.parse_args(argv)
    paths = sorted(Path(a.frames).glob("*.jpg"))[: a.n]
    if not paths:
        sys.exit(f"no jpgs in {a.frames}")
    rules: list = []
    if a.rules:
        from backend.contracts import RulesLabel

        rules = [RulesLabel.model_validate(json.loads(line)) for line in Path(a.rules).read_text().splitlines() if line.strip()]
    res = asyncio.run(amain(paths, rules))
    lat = sorted(r["latency_ms"] for r in res if r["latency_ms"] is not None)
    ok = [r for r in res if r["ok"]]
    print(f"n={len(res)} parsed={len(ok)} ({len(ok)/len(res):.0%}) "
          f"median={statistics.median(lat):.0f}ms p95={lat[min(len(lat)-1, int(len(lat)*0.95))]:.0f}ms "
          f"agree={sum(r['agree'] for r in res)}/{len(res)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
