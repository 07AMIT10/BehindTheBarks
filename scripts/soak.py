"""Soak test: 20 min on /ws, flags RSS growth and message stalls.

    .venv/bin/python scripts/soak.py [--base http://localhost:8000] [--minutes 20] [--pid PID]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def rss_mb(pid: int) -> float | None:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return float(line.split()[1]) / 1024
    except OSError:
        pass
    return None


def find_pid_by_port(port: int) -> int | None:
    try:
        out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if f":{port}" in line and "pid=" in line:
            try:
                return int(line.split("pid=")[1].split(",")[0])
            except ValueError:
                pass
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--pid", type=int, default=None)
    a = ap.parse_args(argv)
    from websockets.sync.client import connect

    pid = a.pid or find_pid_by_port(int(a.base.rsplit(":", 1)[1].split("/")[0]))
    start_rss = rss_mb(pid) if pid else None
    print(f"server pid={pid} start_rss={start_rss}MB")
    counts: Counter = Counter()
    per_minute: Counter = Counter()
    start = last_msg = time.time()
    minute = 0
    fails: list[str] = []
    with connect(a.base.replace("http", "ws") + "/ws", max_size=10_000_000) as ws:
        while (time.time() - start) < a.minutes * 60:
            try:
                m = json.loads(ws.recv(timeout=5.0))
                counts[m.get("type")] += 1
                per_minute[m.get("type")] += 1
                last_msg = time.time()
            except TimeoutError:
                if time.time() - last_msg > 30:
                    fails.append("no WS messages for > 30 s")
                    break
            if time.time() - start >= (minute + 1) * 60:
                minute += 1
                rss = rss_mb(pid) if pid else None
                print(f"min {minute}: {dict(per_minute)} rss={rss}MB", flush=True)
                per_minute.clear()
    end_rss = rss_mb(pid) if pid else None
    print(f"counts={dict(counts)} rss {start_rss} -> {end_rss}MB")
    if start_rss and end_rss and end_rss - start_rss > 100:
        fails.append(f"RSS grew {end_rss - start_rss:.0f}MB (> 100MB)")
    for f in fails:
        print("FAIL:", f)
    print("PASS" if not fails else "FAIL")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
