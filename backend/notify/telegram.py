"""Telegram Bot API notifier for WagWatch.
Supports multi-parent family groups, burst anti-spam grouping, interactive quick actions,
and domestic privacy mode shielding. Never raises: failures come back as NotifyResult(status="failed").
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from backend.contracts import EmotionState
from backend.notify.base import NotifyResult, caption_for
from backend.store import repo as store_repo

log = logging.getLogger("notify")

BURST_WINDOW_S = 180.0  # 3 minutes grouping window


def make_inline_keyboard(household_id: str | None = None, web_base_url: str = "https://watch.furryfolks.co") -> dict:
    hid = household_id or "default"
    return {
        "inline_keyboard": [
            [
                {"text": "▶️ Live Stream", "url": web_base_url},
                {"text": "🔔 Chime", "callback_data": f"cue:doorbell:{hid}"},
            ],
            [
                {"text": "🧸 Squeak", "callback_data": f"cue:squeak:{hid}"},
                {"text": "🍖 Treat", "callback_data": f"treat:{hid}"},
            ],
        ]
    }


class TelegramNotifier:
    is_external: bool = True

    def __init__(
        self,
        token: str,
        chat_id: str = "",
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        profile: dict | None = None,
        household_id: str | None = None,
        db_session_factory: Any | None = None,
        web_base_url: str = "https://watch.furryfolks.co",
    ):
        self._token = token
        self._profile = profile
        self._base = f"https://api.telegram.org/bot{token}"
        self._chat = str(chat_id).strip()
        self._client = client or httpx.AsyncClient(timeout=timeout_s)
        self._household_id = household_id
        self._db_session_factory = db_session_factory
        self._web_base_url = web_base_url

        self._last_burst_start: float = 0.0
        self._last_alert_ts: float = 0.0
        self._burst_count: int = 0
        self._last_msg_ids: dict[str, int] = {}  # chat_id -> message_id

    def _resolve_chats(self) -> list[str]:
        chats: list[str] = []
        if self._db_session_factory and self._household_id:
            try:
                with self._db_session_factory() as session:
                    subs = store_repo.list_telegram_subscribers(session, self._household_id)
                    for sub in subs:
                        if sub.chat_id:
                            chats.append(str(sub.chat_id).strip())
            except Exception as e:
                log.warning("failed to load telegram subscribers from db: %s", e)
        if not chats and self._chat:
            chats.append(self._chat)
        return list(dict.fromkeys(chats))

    async def _send_to_chat(
        self,
        chat_id: str,
        cap: str,
        jpeg: bytes | None,
        reply_markup: dict,
        is_privacy_mode: bool,
    ) -> tuple[bool, int | None, str]:
        # Privacy shielding: suppress photo if privacy mode active
        has_photo = bool(jpeg and not is_privacy_mode)
        final_cap = cap
        if is_privacy_mode and jpeg:
            final_cap += "\n\n🔒 Domestic Privacy Mode active · Photo suppressed"

        markup_json = json.dumps(reply_markup)
        data = {"chat_id": chat_id, "reply_markup": markup_json}

        for _ in range(2):  # 1 retry
            try:
                if has_photo:
                    data["caption"] = final_cap
                    files = {"photo": ("snapshot.jpg", jpeg, "image/jpeg")}
                    r = await self._client.post(f"{self._base}/sendPhoto", data=data, files=files)
                else:
                    data["text"] = final_cap
                    r = await self._client.post(f"{self._base}/sendMessage", data=data)

                if r.status_code == 200 and r.json().get("ok"):
                    res_json = r.json()
                    msg_id = res_json.get("result", {}).get("message_id")
                    return True, msg_id, ""
                detail = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as exc:  # noqa: BLE001
                detail = f"{type(exc).__name__}: {exc}"
        return False, None, detail

    async def _update_burst_caption(
        self,
        chat_id: str,
        message_id: int,
        cap: str,
        reply_markup: dict,
        count: int,
        duration_mins: int,
    ) -> bool:
        burst_cap = cap + f"\n\n⚡ Active burst: {count} barks in {max(1, duration_mins)}m"
        try:
            r = await self._client.post(
                f"{self._base}/editMessageCaption",
                data={
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "caption": burst_cap,
                    "reply_markup": json.dumps(reply_markup),
                },
            )
            if r.status_code == 200 and r.json().get("ok"):
                return True
        except Exception:
            pass
        return False

    async def send(self, state: EmotionState, jpeg: bytes | None, *, is_privacy_mode: bool = False) -> NotifyResult:
        chats = self._resolve_chats()
        if not chats:
            return NotifyResult("failed", "telegram", "No chat ID or subscribers configured")

        now = time.time()
        cap = caption_for(state, self._profile)
        reply_markup = make_inline_keyboard(self._household_id, self._web_base_url)

        # Anti-spam burst logic
        is_bark_burst = state.emotion in ("fearful", "anxious", "aggressive")
        in_burst_window = (now - self._last_alert_ts) < BURST_WINDOW_S and self._burst_count > 0

        if is_bark_burst and in_burst_window and self._last_msg_ids:
            self._burst_count += 1
            duration_mins = int((now - self._last_burst_start) / 60.0)
            all_edited = True
            for chat in chats:
                msg_id = self._last_msg_ids.get(chat)
                if msg_id:
                    ok = await self._update_burst_caption(chat, msg_id, cap, reply_markup, self._burst_count, duration_mins)
                    if not ok:
                        all_edited = False
                else:
                    all_edited = False
            self._last_alert_ts = now
            if all_edited:
                return NotifyResult("sent", "telegram")

        # Fresh alert or failed burst edit
        self._last_burst_start = now
        self._last_alert_ts = now
        self._burst_count = 1
        new_msg_ids: dict[str, int] = {}
        last_error = ""

        any_sent = False
        for chat in chats:
            ok, msg_id, err = await self._send_to_chat(chat, cap, jpeg, reply_markup, is_privacy_mode)
            if ok:
                any_sent = True
                if msg_id:
                    new_msg_ids[chat] = msg_id
            else:
                last_error = err

        self._last_msg_ids = new_msg_ids
        if any_sent:
            return NotifyResult("sent", "telegram")
        log.warning("telegram failed: %s", last_error)
        return NotifyResult("failed", "telegram", last_error)
