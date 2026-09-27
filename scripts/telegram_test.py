"""Send one test photo to the owner's Telegram.

Setup: in Telegram, message @BotFather -> /newbot -> copy the token into TELEGRAM_BOT_TOKEN.
Send any message to your new bot, then open https://api.telegram.org/bot<TOKEN>/getUpdates and copy
result[0].message.chat.id into TELEGRAM_CHAT_ID. Then: .venv/bin/python scripts/telegram_test.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2  # noqa: E402
import numpy as np
from dotenv import load_dotenv

from backend.contracts import EmotionState
from backend.notify.telegram import TelegramNotifier


async def main() -> None:
    load_dotenv()
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    img = np.full((240, 320, 3), (80, 160, 80), np.uint8)
    cv2.putText(img, "Behind The Barks test", (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    jpeg = cv2.imencode(".jpg", img)[1].tobytes()
    state = EmotionState(ts=time.time(), emotion="happy", confidence=0.9, source="rules", reason="Test message.")
    print(await TelegramNotifier(token, chat).send(state, jpeg))


if __name__ == "__main__":
    asyncio.run(main())
