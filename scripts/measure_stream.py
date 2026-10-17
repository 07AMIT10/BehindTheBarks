"""Measure /video MJPEG stream quality and throughput (T5.1).

Connects as a viewer to `/video`, parses the multipart/x-mixed-replace stream,
and computes frame rate, throughput, JPEG size, dimensions, and latency jitter.

Latency measurement method (document for manual run):
    Show a millisecond stopwatch on a laptop (e.g., https://stopwatch.online-timers.com/).
    Point the phone camera at the screen.
    Open the dashboard next to the physical stopwatch on the screen.
    Take a high-speed photo or screenshot capturing both displays.
    Latency = (Laptop stopwatch ms) - (Dashboard video displayed ms).
    Take 5 samples and calculate average and min/max.

Usage:
    .venv/bin/python scripts/measure_stream.py --url http://localhost:8000 --seconds 30
    .venv/bin/python scripts/measure_stream.py --url http://localhost:8000/video?station=kitchen --seconds 60
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

log = logging.getLogger("measure_stream")


def parse_multipart_stream(resp, max_seconds: float):
    boundary = b"--frame"
    buffer = b""
    frames = []
    start_time = time.monotonic()
    last_frame_time = start_time

    while time.monotonic() - start_time < max_seconds:
        chunk = resp.read(8192)
        if not chunk:
            break
        buffer += chunk

        while True:
            first_idx = buffer.find(boundary)
            if first_idx == -1:
                break
            second_idx = buffer.find(boundary, first_idx + len(boundary))
            if second_idx == -1:
                break

            part = buffer[first_idx + len(boundary):second_idx]
            buffer = buffer[second_idx:]

            # Parse headers and JPEG payload in part
            header_end = part.find(b"\r\n\r\n")
            if header_end != -1:
                jpeg_data = part[header_end + 4:].rstrip(b"\r\n")
                if jpeg_data.startswith(b"\xff\xd8") and jpeg_data.endswith(b"\xff\xd9"):
                    now = time.monotonic()
                    gap = now - last_frame_time if frames else 0.0
                    last_frame_time = now

                    # Decode dimensions
                    width, height = 0, 0
                    try:
                        img = Image.open(io.BytesIO(jpeg_data))
                        width, height = img.size
                    except Exception:
                        pass

                    frames.append({
                        "ts": now - start_time,
                        "bytes": len(jpeg_data),
                        "width": width,
                        "height": height,
                        "gap_s": gap,
                    })

    duration = time.monotonic() - start_time
    return frames, duration


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="base URL or video endpoint")
    ap.add_argument("--station", default=None, help="station ID to query")
    ap.add_argument("--token", default=None, help="viewer auth token")
    ap.add_argument("--seconds", type=float, default=60.0, help="measurement duration in seconds")
    ap.add_argument("--output", type=Path, default=None, help="optional path to save JSON metrics")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s")

    video_url = args.url
    if not video_url.endswith("/video") and "/video?" not in video_url:
        video_url = video_url.rstrip("/") + "/video"

    params = []
    if args.station:
        params.append(f"station={urllib.parse.quote(args.station)}")
    if args.token:
        params.append(f"token={urllib.parse.quote(args.token)}")
    if params:
        sep = "&" if "?" in video_url else "?"
        video_url = f"{video_url}{sep}{'&'.join(params)}"

    print(f"Connecting to {video_url} for {args.seconds:.1f} seconds...")
    req = urllib.request.Request(video_url)
    if args.token:
        req.add_header("Authorization", f"Bearer {args.token}")

    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            frames, duration = parse_multipart_stream(resp, args.seconds)
    except Exception as e:
        print(f"Error streaming from {video_url}: {e}", file=sys.stderr)
        return 1

    if not frames:
        print("No frames received during measurement window.")
        return 1

    total_frames = len(frames)
    total_bytes = sum(f["bytes"] for f in frames)
    fps = total_frames / duration if duration > 0 else 0.0
    bytes_per_sec = total_bytes / duration if duration > 0 else 0.0
    kbps = (bytes_per_sec * 8) / 1000.0

    sizes_kb = [f["bytes"] / 1024.0 for f in frames]
    mean_size_kb = statistics.mean(sizes_kb)
    min_size_kb = min(sizes_kb)
    max_size_kb = max(sizes_kb)

    gaps = [f["gap_s"] for f in frames[1:]] if len(frames) > 1 else [0.0]
    max_gap = max(gaps)
    median_gap = statistics.median(gaps)

    common_dim = f"{frames[0]['width']}x{frames[0]['height']}"

    summary = {
        "duration_s": round(duration, 2),
        "total_frames": total_frames,
        "fps": round(fps, 2),
        "total_bytes": total_bytes,
        "bytes_per_sec": round(bytes_per_sec, 1),
        "kbps": round(kbps, 1),
        "mean_frame_kb": round(mean_size_kb, 2),
        "min_frame_kb": round(min_size_kb, 2),
        "max_frame_kb": round(max_size_kb, 2),
        "resolution": common_dim,
        "max_gap_s": round(max_gap, 3),
        "median_gap_s": round(median_gap, 3),
    }

    print("\n--- Stream Measurement Summary ---")
    print(f"Duration:         {summary['duration_s']} s")
    print(f"Total Frames:     {summary['total_frames']}")
    print(f"Average FPS:      {summary['fps']} fps")
    print(f"Resolution:       {summary['resolution']}")
    print(f"Bandwidth:        {summary['kbps']} kbps ({summary['bytes_per_sec']/1024:.1f} KB/s)")
    print(f"Frame Size:       mean {summary['mean_frame_kb']} KB (min {summary['min_frame_kb']} KB, max {summary['max_frame_kb']} KB)")
    print(f"Inter-frame Gap:  median {summary['median_gap_s']} s, max {summary['max_gap_s']} s")
    print("----------------------------------\n")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2))
        print(f"Saved metrics to {args.output}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
