"""Multi-channel notification dispatcher for broadcasting across multiple notification providers."""

from __future__ import annotations

import asyncio
from typing import Sequence

from backend.contracts import EmotionState
from backend.notify.base import Notifier, NotifyResult


class MultiChannelNotifier:
    def __init__(self, notifiers: Sequence[Notifier]):
        self._notifiers = list(notifiers)

    @property
    def is_external(self) -> bool:
        return any(getattr(n, "is_external", False) for n in self._notifiers)

    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult:
        if not self._notifiers:
            return NotifyResult("dashboard_only", "multi", "no channels configured")

        results: list[NotifyResult] = await asyncio.gather(
            *(n.send(state, jpeg) for n in self._notifiers)
        )

        sent = [r for r in results if r.status == "sent"]
        if sent:
            channels = "+".join(r.channel for r in sent)
            return NotifyResult("sent", channels)

        # Check if all were dashboard_only
        if all(r.status == "dashboard_only" for r in results):
            return NotifyResult("dashboard_only", "multi", "dashboard only")

        # Otherwise at least one failed
        failed_details = [f"{r.channel}: {r.detail or r.status}" for r in results if r.status != "sent"]
        return NotifyResult("failed", "multi", "; ".join(failed_details))
