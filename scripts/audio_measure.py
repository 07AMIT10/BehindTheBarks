#!/usr/bin/env python3
"""Measure the live audio stream on the phone (Full mode): docs/AUDIO_MEASUREMENTS.md.

Needs a pin-mode backend already running on 127.0.0.1:8000 (WEB_PIPELINE=remote) and the phone awake,
unlocked, with WagWatch installed. Runs three ON/OFF pairs of 60 s windows, alternating, and prints one
JSON object on stdout. ON = one /audio listener connected for the whole window; OFF = no listener and no
other viewer. No /video viewer in either window, so the phone's video path is idle in both.

Measured per window: app CPU (utime + stime ticks from /proc/<pid>/stat over wall time, one core = 100 %),
listener payload bytes/s at the viewer (forwarded frames without the 0x02 byte; WebSocket framing is not
counted), frame arrival interval p50/p95/max, seq gaps and rejected frames, server counter deltas
(audio_frames, audio_bytes), battery level, temperature and charging status at each window boundary, and
RMS / peak of the first 10 s of decoded audio from the first ON window.

Not measured: end-to-end latency (no common clock between the phone's capture and the viewer's playback).

    .venv/bin/python scripts/audio_measure.py > /tmp/audio_measure.json
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import phone_acceptance as pa  # noqa: E402  (helpers: adb, status, AudioListener, decode_audio_frames)

WINDOW_S = 60.0
PAIRS = 3


def app_pid() -> str:
    return pa.adb("shell", "pidof", pa.PKG).strip()


def cpu_ticks(pid: str) -> int:
    # /proc/<pid>/stat: fields after the ')' start at field 3 (state), so utime (14) is index 11, stime (15) index 12.
    fields = pa.adb("shell", f"cat /proc/{pid}/stat").split(")")[-1].split()
    return int(fields[11]) + int(fields[12])


def battery() -> dict:
    out = pa.adb("shell", "dumpsys", "battery")
    level = pa.phone_battery()
    status = re.search(r"\bstatus:\s*(\d+)", out)
    ac = re.search(r"AC powered:\s*(\w+)", out)
    usb = re.search(r"USB powered:\s*(\w+)", out)
    return {**level, "status_code": int(status.group(1)) if status else None,
            "ac": ac.group(1) if ac else None, "usb": usb.group(1) if usb else None}


def counters() -> tuple[int, int]:
    st = pa.status()
    return int(pa.find_key(st, "audio_frames") or 0), int(pa.find_key(st, "audio_bytes") or 0)


def percentile(sorted_vals: list[float], q: float) -> float:
    return sorted_vals[int(q * (len(sorted_vals) - 1))] if sorted_vals else 0.0


def window(index: int, listen: bool, pid: str, clk: int) -> dict:
    if pa.status().get("effective_mode") != "full":
        raise RuntimeError(f"window {index}: effective mode is not full")
    lis = None
    if listen:
        lis = pa.AudioListener()
        lis.start()
        lis.connected.wait(6)
        if lis.error or not lis.opened_at:
            raise RuntimeError(f"window {index}: /audio did not open: {lis.error}")
    frames0, bytes0 = counters()
    bat0 = battery()
    t0 = time.monotonic()
    c0 = cpu_ticks(pid)
    time.sleep(WINDOW_S)
    c1 = cpu_ticks(pid)
    t1 = time.monotonic()
    frames1, bytes1 = counters()
    bat1 = battery()
    if app_pid() != pid:
        raise RuntimeError(f"window {index}: app pid changed during the window")
    res: dict = {"window": index, "listen": listen, "wall_s": round(t1 - t0, 2),
                 "cpu_pct": round((c1 - c0) / clk / (t1 - t0) * 100, 2),
                 "battery_before": bat0, "battery_after": bat1,
                 "server_audio_frames_delta": frames1 - frames0, "server_audio_bytes_delta": bytes1 - bytes0}
    if lis is not None:
        lis.stop()
        frames = lis.frames
        payload = sum(len(raw) for _, raw in frames)
        times = [t for t, _ in frames]
        gaps_ms = sorted((b - a) * 1000 for a, b in zip(times, times[1:]))
        stats = pa.decode_audio_frames(frames)
        res.update({
            "hello": lis.hello, "frames": len(frames), "frames_per_s": round(len(frames) / (t1 - t0), 2),
            "payload_bytes": payload, "payload_bytes_per_s": round(payload / (t1 - t0), 1),
            "interarrival_ms_p50": round(percentile(gaps_ms, 0.50), 1),
            "interarrival_ms_p95": round(percentile(gaps_ms, 0.95), 1),
            "interarrival_ms_max": round(gaps_ms[-1], 1) if gaps_ms else 0.0,
            "seq_gap_events": stats["gap_events"], "seq_missing": stats["missing_seq"],
            "rejected": stats["errors"], "decoded_samples": stats["samples"],
        })
        if frames:
            first = frames[0][0]
            head = pa.decode_audio_frames([f for f in frames if f[0] - first <= 10.0])
            res["first10s_rms"] = round(head["rms"], 1)
            res["first10s_peak"] = head["peak"]
            res["first10s_frames"] = head["frames"]
    return res


def main() -> int:
    if not pa.status():
        print("no backend answers on 127.0.0.1:8000", file=sys.stderr)
        return 2
    pa.preflight()
    pa.ensure_adb_reverse()
    recovery = pa.recover_full_session()
    pid = app_pid()
    if not pid:
        print(f"WagWatch is not running after recovery: {recovery}", file=sys.stderr)
        return 2
    clk = int(pa.adb("shell", "getconf", "CLK_TCK").strip() or "100")
    out: dict = {"recovery": recovery, "clk_tck": clk, "window_s": WINDOW_S, "pid": pid,
                 "device": pa.adb("shell", "getprop", "ro.product.model").strip(),
                 "android": pa.adb("shell", "getprop", "ro.build.version.release").strip(),
                 "battery_start": battery(), "windows": []}
    try:
        for i in range(PAIRS):
            for listen in (True, False):
                out["windows"].append(window(len(out["windows"]) + 1, listen, pid, clk))
                print(f"window {out['windows'][-1]['window']} listen={listen} done", file=sys.stderr, flush=True)
    finally:
        out["battery_end"] = battery()
        pa.force_stop()
        pa.adb("reverse", "--remove-all")
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
