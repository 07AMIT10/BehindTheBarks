"""WhatsApp Cloud API notifier. Never raises: failures come back as NotifyResult(status="failed")."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from backend.contracts import EmotionState
from backend.notify.base import NotifyResult, caption_for

log = logging.getLogger("notify")


class WhatsAppNotifier:
    is_external: bool = True

    def __init__(
        self,
        token: str,
        phone_number_id: str,
        recipient: str,
        api_version: str = "v18.0",
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        profile: dict[str, Any] | None = None,
    ):
        self._token = token
        self._phone_number_id = phone_number_id
        # Strip '+' or spaces from recipient phone number
        self._recipient = "".join(c for c in recipient if c.isdigit())
        self._api_version = api_version.lstrip("/")
        self._base = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}"
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._profile = profile

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
        }

    async def _send_media(self, jpeg: bytes, cap: str) -> httpx.Response:
        # Step 1: Upload media to WhatsApp Cloud API
        media_resp = await self._client.post(
            f"{self._base}/media",
            headers=self._headers,
            data={"messaging_product": "whatsapp", "type": "image/jpeg"},
            files={"file": ("snapshot.jpg", jpeg, "image/jpeg")},
        )
        if media_resp.status_code in (200, 201):
            media_id = media_resp.json().get("id")
            if media_id:
                # Step 2: Send message with media ID
                payload = {
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": self._recipient,
                    "type": "image",
                    "image": {
                        "id": media_id,
                        "caption": cap,
                    },
                }
                return await self._client.post(
                    f"{self._base}/messages",
                    headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                    json=payload,
                )
        # Fallback to text message if media upload returned unexpected result
        return await self._send_text(cap)

    async def _send_text(self, cap: str) -> httpx.Response:
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self._recipient,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": cap,
            },
        }
        return await self._client.post(
            f"{self._base}/messages",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
            json=payload,
        )

    async def _once(self, state: EmotionState, jpeg: bytes | None) -> httpx.Response:
        cap = caption_for(state, self._profile)
        if jpeg:
            return await self._send_media(jpeg, cap)
        return await self._send_text(cap)

    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult:
        detail = ""
        for _ in range(2):  # one retry
            try:
                r = await self._once(state, jpeg)
                if r.status_code in (200, 201) and r.json().get("messages"):
                    return NotifyResult("sent", "whatsapp")
                detail = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
        log.warning("whatsapp failed: %s", detail)
        return NotifyResult("failed", "whatsapp", detail)
