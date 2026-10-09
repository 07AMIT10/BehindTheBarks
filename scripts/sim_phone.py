"""Simulated WagWatch phone for backend development and tests.

Talks the same `/ingest-events` WebSocket protocol as the Android app: a `hello`,
then one preview JPEG (binary 0x01 + JPEG) and one FrameEvent envelope every 200 ms,
then ends the session the way you ask it to. This is the scripted replacement for the
throwaway simulated client used while validating the 2026-10-10 plan.

    .venv/bin/python scripts/sim_phone.py --seconds 5 --stop bye        # deliberate stop
    .venv/bin/python scripts/sim_phone.py --seconds 5 --stop close1000  # clean close, no bye
    .venv/bin/python scripts/sim_phone.py --seconds 5 --stop drop       # abrupt TCP drop (1006)

What the backend logs for each ending (backend/web/runtime.py `phone_close`):
    bye       -> "phone stopped: <device> (code 1000)"
    close1000 -> "phone stopped: <device> (code 1000)"
    drop      -> "phone disconnected: <device> (code 1006)"
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import time

import websockets

log = logging.getLogger("sim_phone")

PROTO = 1
# A tiny valid red JPEG (32x24, q40) so /video and notification snapshots have real bytes.
JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABQODxIPDRQSEBIXFRQYHjIhHhwcHj0sLiQySUBMS0dARkVQWnNiUFVt"
    "VkVGZIhlbXd7gYKBTmCNl4x9lnN+gXz/2wBDARUXFx4aHjshITt8U0ZTfHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8"
    "fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHz/wAARCAAYACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAA"
    "AAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwR"
    "VS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3"
    "+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpj"
    "ZGVmZ2hpanN2dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX"
    "2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDBooorkPogooooAKKKKACiiigD/9k="
)


def frame_event(ts: float) -> str:
    """A minimal but contract-valid FrameEvent (backend/contracts.py, extra="forbid")."""
    return json.dumps(
        {
            "type": "frame",
            "data": {
                "ts": ts,
                "source": "live",
                "dog_detected": True,
                "bbox": [10.0, 20.0, 300.0, 420.0],
                "bbox_conf": 0.91,
                "body_keypoints": {"tail_base": [150.0, 300.0, 0.9]},
                "face_landmarks": None,
                "features": {
                    "tail_height": 0.4,
                    "tail_wag_hz": 2.1,
                    "ear_position": "up",
                    "mouth_open": 0.6,
                    "body_lowering": 0.1,
                    "motion_energy": 0.7,
                    "in_feeding_zone": True,
                },
            },
        }
    )


async def run(url: str, device: str, seconds: float, stop: str, period_s: float) -> int:
    async with websockets.connect(url) as ws:
        hello = {
            "type": "hello",
            "proto": PROTO,
            "device": device,
            "phone_time": time.time(),
            "models": {"detect": "sim-yolo", "pose": "sim-rtmpose"},
            "profile": "mobile",
        }
        await ws.send(json.dumps(hello))
        ack = json.loads(await ws.recv())
        log.info("hello_ack: server_time=%.3f (ok=%s)", ack.get("server_time", -1), ack.get("type") == "hello_ack")

        jpeg = base64.b64decode(JPEG_B64)
        deadline = time.monotonic() + seconds
        sent = 0
        while time.monotonic() < deadline:
            ts = time.time()
            await ws.send(b"\x01" + jpeg)  # KIND_PREVIEW
            await ws.send(frame_event(ts))
            sent += 1
            await asyncio.sleep(period_s)
        log.info("sent %d frame+preview pairs", sent)

        if stop == "bye":
            await ws.send(json.dumps({"type": "bye"}))
            log.info("sent bye, closing with code 1000")
            await ws.close(code=1000, reason="Client stopped")
        elif stop == "close1000":
            log.info("closing with code 1000 (no bye)")
            await ws.close(code=1000, reason="Client stopped")
        else:  # drop: kill the TCP connection without a close frame (server sees 1006)
            log.info("dropping the TCP connection without a close frame")
            ws.transport.close()
            await asyncio.sleep(0.2)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8000/ingest-events")
    ap.add_argument("--device", default="SimPhone")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--stop", choices=["bye", "close1000", "drop"], default="close1000")
    ap.add_argument("--period", type=float, default=0.2, help="seconds between frame+preview pairs")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if not args.url.startswith(("ws://", "wss://")):
        print(f"url must be a ws:// or wss:// URL, got {args.url!r}", file=sys.stderr)
        return 2
    return asyncio.run(run(args.url, args.device, args.seconds, args.stop, args.period))


if __name__ == "__main__":
    sys.exit(main())
