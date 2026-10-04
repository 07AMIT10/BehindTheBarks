"""Telegram Bot API notifier. Never raises: failures come back as NotifyResult(status="failed")."""

from __future__ import annotations

import logging

import httpx

from backend.contracts import EmotionState
from backend.notify.base import NotifyResult, caption_for

log = logging.getLogger("notify")


class TelegramNotifier:
    is_external: bool = True

    def __init__(self, token: str, chat_id: str, client: httpx.AsyncClient | None = None, timeout_s: float = 10.0,
                 profile: dict | None = None):
        self._profile = profile
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat = chat_id
        self._client = client or httpx.AsyncClient(timeout=timeout_s)

    async def _once(self, state: EmotionState, jpeg: bytes | None) -> httpx.Response:
        cap = caption_for(state, self._profile)
        if jpeg:
            return await self._client.post(f"{self._base}/sendPhoto", data={"chat_id": self._chat, "caption": cap},
                                           files={"photo": ("snapshot.jpg", jpeg, "image/jpeg")})
        return await self._client.post(f"{self._base}/sendMessage", data={"chat_id": self._chat, "text": cap})

    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult:
        detail = ""
        for _ in range(2):  # one retry
            try:
                r = await self._once(state, jpeg)
                if r.status_code == 200 and r.json().get("ok"):
                    return NotifyResult("sent", "telegram")
                detail = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
        log.warning("telegram failed: %s", detail)
        return NotifyResult("failed", "telegram", detail)
