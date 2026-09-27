from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from backend.contracts import EmotionState

EMOJI = {"happy": "😊", "excited": "🤩", "relaxed": "😌", "anxious": "😟", "fearful": "😨",
         "aggressive": "😠", "disinterested": "😐", "unknown": "❔"}
NEGATIVE = frozenset({"anxious", "fearful", "aggressive", "disinterested"})
_SOURCE = {"fused": "Fused", "llm": "AI", "rules": "Rules"}


@dataclass
class NotifyResult:
    status: Literal["sent", "failed", "dashboard_only"]
    channel: str
    detail: str = ""


class Notifier(Protocol):
    async def send(self, state: EmotionState, jpeg: bytes | None) -> NotifyResult: ...


def caption_for(state: EmotionState, profile: dict | None = None) -> str:
    p = profile or {}
    dog = p.get("dog_name") or "Your dog"
    verb = "seems" if state.emotion in NEGATIVE else "is"
    when = datetime.fromtimestamp(state.ts).strftime("%H:%M")
    place = " · ".join(x for x in (p.get("location"), p.get("zone_label")) if x)
    tail = f"{place} · " if place else ""
    return (f"{EMOJI.get(state.emotion, '')} {dog} {verb} {state.emotion} · {round(state.confidence * 100)}%\n"
            f"{state.reason}\n{tail}{_SOURCE[state.source]} reading · {when}")
