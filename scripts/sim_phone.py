"""Simulated WagWatch phone for backend development and tests.

Talks the same `/ingest-events` WebSocket protocol as the Android app: a `hello`,
challenge-response authentication (proto 2) or proto 1, then preview JPEGs (binary 0x01 + JPEG)
and FrameEvent envelopes every period (default 200 ms), then ends the session.

Usage examples:
    # Legacy / direct proto 1
    .venv/bin/python scripts/sim_phone.py --seconds 5 --stop close1000

    # Households mode: pair once with a pairing code and persist key
    .venv/bin/python scripts/sim_phone.py --pair-code WAG-1234 --keyfile /tmp/phone1.json --seconds 10

    # Reconnect with existing keyfile
    .venv/bin/python scripts/sim_phone.py --keyfile /tmp/phone1.json --seconds 10
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
import websockets

log = logging.getLogger("sim_phone")

AUTH_PREFIX = b"btb-auth-v1|"

# A tiny valid red JPEG (32x24, q40) so /video and notification snapshots have real bytes.
JPEG_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABQODxIPDRQSEBIXFRQYHjIhHhwcHj0sLiQySUBMS0dARkVQWnNiUFVt"
    "VkVGZIhlbXd7gYKBTmCNl4x9lnN+gXz/2wBDARUXFx4aHjshITt8U0ZTfHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8"
    "fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHx8fHz/wAARCAAYACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAA"
    "AAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwR"
    "VS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWG"
    "h4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3"
    "+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpj"
    "ZGVmZ2hpanN2dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX"
    "2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDBooorkPogooooAKKKKACiiigD/9k="
)


def frame_event(ts: float) -> str:
    """A minimal but contract-valid FrameEvent (backend/contracts.py, extra='forbid')."""
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


def pair_station(pair_url: str, code: str, key: ec.EllipticCurvePrivateKey, device: str) -> dict:
    spki = base64.b64encode(
        key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).decode()
    payload = json.dumps({"code": code, "pubkey_spki_b64": spki, "device_name": device}).encode("utf-8")
    req = urllib.request.Request(pair_url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_or_create_key(keyfile: Path | None, pair_code: str | None, pair_url: str, device: str) -> tuple[ec.EllipticCurvePrivateKey, str | None, str | None]:
    if keyfile and keyfile.exists():
        data = json.loads(keyfile.read_text())
        key = serialization.load_pem_private_key(data["private_key_pem"].encode(), password=None)
        return key, data.get("station_id"), data.get("household_id")

    key = ec.generate_private_key(ec.SECP256R1())
    station_id, household_id = None, None

    if pair_code:
        log.info("Pairing station with code %s via %s...", pair_code, pair_url)
        res = pair_station(pair_url, pair_code, key, device)
        station_id, household_id = res["station_id"], res["household_id"]
        log.info("Pairing successful: station_id=%s, household_id=%s", station_id, household_id)

    if keyfile:
        pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        keyfile.write_text(json.dumps({
            "station_id": station_id,
            "household_id": household_id,
            "device": device,
            "private_key_pem": pem,
        }, indent=2))
        log.info("Saved station key to %s", keyfile)

    return key, station_id, household_id


async def run(
    url: str,
    device: str,
    seconds: float,
    stop: str,
    period_s: float,
    keyfile: Path | None = None,
    pair_code: str | None = None,
    pair_url: str | None = None,
) -> int:
    if not pair_url:
        pair_url = url.replace("ws://", "http://").replace("wss://", "https://").replace("/ingest-events", "/pair")

    key, station_id, _ = load_or_create_key(keyfile, pair_code, pair_url, device)
    proto = 2 if station_id else 1

    async with websockets.connect(url) as ws:
        hello: dict = {
            "type": "hello",
            "proto": proto,
            "device": device,
            "phone_time": time.time(),
            "models": {"detect": "sim-yolo", "pose": "sim-rtmpose"},
            "profile": "mobile",
        }
        if station_id:
            hello["station_id"] = station_id

        await ws.send(json.dumps(hello))

        if proto == 2:
            challenge = json.loads(await ws.recv())
            if challenge.get("type") != "challenge":
                log.error("Expected challenge, got: %s", challenge)
                return 1
            nonce_b64 = challenge["nonce"]
            msg = AUTH_PREFIX + base64.b64decode(nonce_b64) + b"|" + station_id.encode() + b"|" + device.encode()
            sig = base64.b64encode(key.sign(msg, ec.ECDSA(hashes.SHA256()))).decode()
            await ws.send(json.dumps({"type": "auth", "sig": sig}))

        ack = json.loads(await ws.recv())
        log.info("hello_ack: server_time=%.3f (ok=%s)", ack.get("server_time", -1), ack.get("type") == "hello_ack")

        # Background task to drain downlink messages
        downlinks_received = 0

        async def drain_downlinks():
            nonlocal downlinks_received
            try:
                async for raw_msg in ws:
                    downlinks_received += 1
                    try:
                        m = json.loads(raw_msg)
                        log.debug("downlink received: %s", m.get("type"))
                    except Exception:
                        pass
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.debug("drain_downlinks ended: %s", e)

        drain_task = asyncio.create_task(drain_downlinks())

        jpeg = base64.b64decode(JPEG_B64)
        deadline = time.monotonic() + seconds
        sent = 0
        try:
            while time.monotonic() < deadline:
                ts = time.time()
                await ws.send(b"\x01" + jpeg)  # KIND_PREVIEW
                await ws.send(frame_event(ts))
                sent += 1
                await asyncio.sleep(period_s)
        finally:
            drain_task.cancel()

        log.info("sent %d frame+preview pairs (received %d downlinks)", sent, downlinks_received)

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
    ap.add_argument("--keyfile", type=Path, default=None, help="path to JSON keyfile to persist/load station key")
    ap.add_argument("--pair-code", type=str, default=None, help="one-time pairing code to pair station")
    ap.add_argument("--pair-url", type=str, default=None, help="HTTP URL to pair endpoint (defaults to http equivalent of --url)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if not args.url.startswith(("ws://", "wss://")):
        print(f"url must be a ws:// or wss:// URL, got {args.url!r}", file=sys.stderr)
        return 2
    return asyncio.run(run(
        url=args.url,
        device=args.device,
        seconds=args.seconds,
        stop=args.stop,
        period_s=args.period,
        keyfile=args.keyfile,
        pair_code=args.pair_code,
        pair_url=args.pair_url,
    ))


if __name__ == "__main__":
    sys.exit(main())
