"""Test script for Telegram and WhatsApp push notifications.

Usage:
  # Test with credentials from .env:
  .venv/bin/python scripts/notify_test.py

  # Test specific channel:
  .venv/bin/python scripts/notify_test.py --mode telegram
  .venv/bin/python scripts/notify_test.py --mode whatsapp
  .venv/bin/python scripts/notify_test.py --mode multi
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from dotenv import load_dotenv

from backend.contracts import EmotionState
from backend.notify import build_notifier
from backend.web.settings import load_config


def make_test_frame(label: str = "WagWatch Test") -> bytes:
    """Generate a clean synthetic dog camera snapshot."""
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    # Background slate
    img[:] = (35, 30, 30)

    # Decorative header bar
    cv2.rectangle(img, (0, 0), (640, 60), (50, 45, 45), -1)
    cv2.putText(img, "WAGWATCH // ON-DEVICE PERCEPTION", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

    # Center bounding box simulating dog detection
    cv2.rectangle(img, (140, 90), (500, 420), (0, 200, 100), 2)
    cv2.putText(img, "DOG: Bruno (0.94)", (145, 80),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 100), 2)

    # Simulated pose skeleton points
    points = [
        (250, 170), (280, 150), (220, 150), # nose, ears
        (250, 220), (380, 240),             # neck, tail base
        (430, 180),                         # tail tip (wagging high)
        (230, 380), (270, 380),             # front paws
        (370, 380), (410, 380),             # back paws
    ]
    for pt in points:
        cv2.circle(img, pt, 5, (0, 255, 255), -1)

    # Status text
    ts_str = time.strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(img, f"Status: {label}", (20, 455),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    cv2.putText(img, ts_str, (430, 455),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (160, 160, 160), 1)

    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return buf.tobytes()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test WagWatch push notification dispatchers.")
    parser.add_argument("--mode", choices=["telegram", "whatsapp", "multi", "auto"], default="auto",
                        help="Notification channel to test (default: auto detects from environment)")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config()

    env_overrides = {}
    if args.mode != "auto":
        env_overrides["NOTIFY_MODE"] = args.mode

    notifier = build_notifier(cfg, env=env_overrides if env_overrides else None)
    notifier_name = type(notifier).__name__

    print(f"==> Active Notifier: {notifier_name}")

    if notifier_name == "DashboardOnlyNotifier":
        print("\n[NOTE] No external messaging credentials found.")
        print("To enable Telegram:")
        print("  Add to .env: TELEGRAM_BOT_TOKEN=... and TELEGRAM_CHAT_ID=...")
        print("  Set: NOTIFY_MODE=telegram")
        print("\nTo enable WhatsApp:")
        print("  Add to .env: WHATSAPP_TOKEN=..., WHATSAPP_PHONE_NUMBER_ID=..., WHATSAPP_RECIPIENT_PHONE=...")
        print("  Set: NOTIFY_MODE=whatsapp")
        return

    print("==> Generating synthetic test camera frame...")
    jpeg = make_test_frame("Happy Alert Test")

    state = EmotionState(
        ts=time.time(),
        emotion="happy",
        confidence=0.92,
        source="fused",
        reason="Rapid tail wagging (3.4 Hz), relaxed open mouth, upright stance.",
    )

    print("==> Dispatching notification...")
    result = await notifier.send(state, jpeg)

    print("\n---------------- Notification Result ----------------")
    print(f"Status:  {result.status.upper()}")
    print(f"Channel: {result.channel}")
    if result.detail:
        print(f"Detail:  {result.detail}")
    print("-----------------------------------------------------")

    if result.status == "sent":
        print("\nSUCCESS! Check your chat to view the incoming photo and emotion reading.")
    else:
        print(f"\nDelivery failed: {result.detail}")


if __name__ == "__main__":
    asyncio.run(main())
