from __future__ import annotations

import os
from typing import Mapping

from backend.notify.base import EMOJI, NEGATIVE, Notifier, NotifyResult, caption_for
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.notify.multi import MultiChannelNotifier
from backend.notify.telegram import TelegramNotifier
from backend.notify.whatsapp import WhatsAppNotifier

__all__ = [
    "EMOJI",
    "NEGATIVE",
    "Notifier",
    "NotifyResult",
    "caption_for",
    "build_notifier",
    "DashboardOnlyNotifier",
    "TelegramNotifier",
    "WhatsAppNotifier",
    "MultiChannelNotifier",
]


def build_notifier(cfg: dict, env: Mapping[str, str] | None = None) -> Notifier:
    env = os.environ if env is None else env
    web_cfg = cfg.get("web", {})
    notify_cfg = web_cfg.get("notify", {})
    profile = web_cfg.get("profile")

    mode = env.get("NOTIFY_MODE") or notify_cfg.get("mode", "dashboard_only")
    mode = mode.strip().lower()

    # Telegram credentials (env overrides config)
    tg_cfg = notify_cfg.get("telegram") if isinstance(notify_cfg.get("telegram"), dict) else {}
    tg_token = env.get("TELEGRAM_BOT_TOKEN") or tg_cfg.get("token", "")
    tg_chat = env.get("TELEGRAM_CHAT_ID") or tg_cfg.get("chat_id", "")

    # WhatsApp credentials (env overrides config)
    wa_cfg = notify_cfg.get("whatsapp") if isinstance(notify_cfg.get("whatsapp"), dict) else {}
    wa_token = env.get("WHATSAPP_TOKEN") or env.get("WHATSAPP_API_TOKEN") or wa_cfg.get("token", "")
    wa_phone_id = (
        env.get("WHATSAPP_PHONE_NUMBER_ID")
        or env.get("WHATSAPP_PHONE_ID")
        or wa_cfg.get("phone_number_id", "")
    )
    wa_recipient = (
        env.get("WHATSAPP_RECIPIENT_PHONE")
        or env.get("WHATSAPP_RECIPIENT")
        or wa_cfg.get("recipient", "")
    )
    wa_version = env.get("WHATSAPP_API_VERSION") or wa_cfg.get("api_version", "v18.0")

    def _tg() -> TelegramNotifier | None:
        if tg_token and tg_chat:
            return TelegramNotifier(tg_token, tg_chat, profile=profile)
        return None

    def _wa() -> WhatsAppNotifier | None:
        if wa_token and wa_phone_id and wa_recipient:
            return WhatsAppNotifier(
                token=wa_token,
                phone_number_id=wa_phone_id,
                recipient=wa_recipient,
                api_version=wa_version,
                profile=profile,
            )
        return None

    if mode == "telegram":
        tg = _tg()
        return tg if tg is not None else DashboardOnlyNotifier()

    if mode == "whatsapp":
        wa = _wa()
        return wa if wa is not None else DashboardOnlyNotifier()

    if mode in ("multi", "all"):
        channels: list[Notifier] = []
        tg = _tg()
        if tg is not None:
            channels.append(tg)
        wa = _wa()
        if wa is not None:
            channels.append(wa)

        if len(channels) > 1:
            return MultiChannelNotifier(channels)
        if len(channels) == 1:
            return channels[0]
        return DashboardOnlyNotifier()

    return DashboardOnlyNotifier()
