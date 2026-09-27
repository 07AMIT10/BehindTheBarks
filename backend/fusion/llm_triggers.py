"""When to call the LLM (CLAUDE.md "LLM interpreter"). Pure; `now` passed in."""

from __future__ import annotations

import math

from backend.contracts import AudioEvent, RulesLabel

_PRIORITY = {"treat": 3, "audio": 2, "rules_change": 1}
_DOG_SOUNDS = {"bark", "yip", "growl", "whimper", "howl"}


class TriggerPolicy:
    def __init__(self, min_interval_s: float = 3.0, heartbeat_s: float = 10.0, audio_score: float = 0.6):
        self.min_interval_s, self.heartbeat_s, self.audio_score = min_interval_s, heartbeat_s, audio_score
        self._last_call = -math.inf
        self._pending: str | None = None
        self._last_rules: str | None = None

    @classmethod
    def from_config(cls, cfg: dict) -> "TriggerPolicy":
        llm = cfg["web"]["llm"]
        return cls(llm["min_interval_s"], llm["heartbeat_s"], llm["audio_trigger_score"])

    def _set(self, reason: str) -> None:
        if self._pending is None or _PRIORITY[reason] >= _PRIORITY[self._pending]:
            self._pending = reason

    def note_treat(self) -> None:
        self._set("treat")

    def note_audio(self, ev: AudioEvent) -> None:
        if ev.label in _DOG_SOUNDS and ev.score > self.audio_score:
            self._set("audio")

    def note_rules(self, r: RulesLabel) -> None:
        if self._last_rules is not None and r.emotion != self._last_rules:
            self._set("rules_change")
        self._last_rules = r.emotion

    def due(self, now: float, in_flight: bool) -> str | None:
        if in_flight or now - self._last_call < self.min_interval_s - 1e-9:
            return None
        reason = self._pending or ("heartbeat" if now - self._last_call >= self.heartbeat_s - 1e-9 else None)
        if reason:
            self._last_call, self._pending = now, None
        return reason
