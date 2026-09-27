from __future__ import annotations

import os
from typing import Mapping

from backend.notify.base import EMOJI, NEGATIVE, Notifier, NotifyResult, caption_for
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.notify.telegram import TelegramNotifier

__all__ = ["EMOJI", "NEGATIVE", "Notifier", "NotifyResult", "caption_for", "build_notifier",
           "DashboardOnlyNotifier", "TelegramNotifier"]


def build_notifier(cfg: dict, env: Mapping[str, str] | None = None) -> Notifier:
    env = os.environ if env is None else env
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if cfg["web"]["notify"]["mode"] == "telegram" and token and chat:
        return TelegramNotifier(token, chat, profile=cfg["web"]["profile"])
    return DashboardOnlyNotifier()
