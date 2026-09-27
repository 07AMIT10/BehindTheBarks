"""Integration checklist against a RUNNING backend (Person A's real pipeline or the mock).

    .venv/bin/python scripts/check_integration.py [--base http://localhost:8000] [--seconds 30]

Samples /status, records /ws envelopes, validates frame/audio/rules/emotion against contracts.py,
reports per-type rates + WS latency. Exit 0 = PASS, 1 = FAIL. Set web.pipeline: real in config.yaml
first, with a fallback clip in file mode (then the webcam).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from backend.contracts import AudioEvent, EmotionState, FrameEvent, RulesLabel  # noqa: E402
from backend.web.hub import MESSAGE_TYPES  # noqa: E402

CHECKED = {"frame": FrameEvent, "audio": AudioEvent, "rules": RulesLabel, "emotion": EmotionState}
MIN_RATES = {"frame": 3.0, "rules": 3.0}  # per second; audio/emotion are event-driven
MAX_LATENCY_S = 2.0


def analyse(messages: list[dict], status: dict, now: float) -> dict:
    problems: list[str] = []
    invalid = 0
    latencies: list[float] = []
    kinds = Counter()
    first_ts, last_ts = None, None
    for m in messages:
        kinds[m.get("type")] += 1
        model = CHECKED.get(m.get("type"))
        if model is not None:
            try:
                model.model_validate(m.get("data"))
            except ValidationError as exc:
                invalid += 1
                problems.append(f"invalid {m.get('type')}: {exc.errors()[0]['loc']}: {exc.errors()[0]['msg']}")
            else:
                # WS latency uses the envelope ts (hub send time), not data.ts: a 1 Hz live
                # rebroadcast of an unchanged state correctly carries the original state time.
                ets = m.get("ts")
                if isinstance(ets, (int, float)):
                    latencies.append(max(0.0, m.get("_received", now) - ets))
        if isinstance(m.get("ts"), (int, float)):
            first_ts = m["ts"] if first_ts is None else min(first_ts, m["ts"])
            last_ts = m["ts"] if last_ts is None else max(last_ts, m["ts"])
    span = max(0.001, (last_ts or now) - (first_ts or now))
    rates = {k: round(kinds.get(k, 0) / span, 2) for k in CHECKED}
    for k, minimum in MIN_RATES.items():
        if rates[k] < minimum:
            problems.append(f"{k} rate {rates[k]}/s below {minimum}/s (expected ~8 fps in file mode)")
    max_lat = round(max(latencies, default=0.0), 3)
    if max_lat > MAX_LATENCY_S:
        problems.append(f"max WS latency {max_lat}s above {MAX_LATENCY_S}s (event loop blocked?)")
    if not messages:
        problems.append("no messages received: is the pipeline running?")
    unknown = [k for k in kinds if k not in MESSAGE_TYPES]
    if unknown:
        problems.append(f"unknown envelope types: {unknown}")
    return {"ok": not problems, "problems": problems, "invalid": invalid, "rates": rates,
            "max_latency_s": max_lat, "counts": dict(kinds), "pipeline": status.get("pipeline")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--seconds", type=float, default=30.0)
    a = ap.parse_args(argv)

    def get(path: str):
        with urllib.request.urlopen(a.base + path, timeout=10) as r:
            return json.load(r)

    try:
        status = get("/status")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: backend unreachable: {exc}")
        return 1
    print(f"pipeline={status.get('pipeline')} mode={status.get('mode')} fps={status.get('fps')}")
    try:
        from websockets.sync.client import connect
    except ImportError:
        print("FAIL: pip install websockets (in requirements-web.txt)")
        return 1
    messages: list[dict] = []
    deadline = time.time() + a.seconds
    with connect(a.base.replace("http", "ws") + "/ws", max_size=10_000_000) as ws:
        while time.time() < deadline:
            try:
                messages.append({"_received": time.time(),
                                   **json.loads(ws.recv(timeout=max(0.1, deadline - time.time())))})
            except TimeoutError:
                break
    rep = analyse(messages, status, time.time())
    print(f"counts={rep['counts']} rates={rep['rates']} max_latency={rep['max_latency_s']}s invalid={rep['invalid']}")
    for p in rep["problems"]:
        print("FAIL:", p)
    print("PASS" if rep["ok"] else "FAIL")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
