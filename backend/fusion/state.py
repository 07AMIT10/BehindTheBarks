"""Final label + state machine (Web). Pure: no I/O, no clock; `now` is always passed in.

combine(): CLAUDE.md "Final label". FusionState: persistence (>= persist_s), per-emotion cooldowns,
always-notify emotions, session timeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

from backend.contracts import EmotionState, FrameEvent, LLMResult, RulesLabel


@dataclass
class StateConfig:
    persist_s: float = 3.0
    no_dog_unknown_s: float = 2.0
    llm_override_conf: float = 0.7
    llm_stale_s: float = 12.0
    cooldown_s: float = 60.0
    cooldowns: dict[str, float] = field(default_factory=dict)
    always_notify: tuple[str, ...] = ("fearful", "aggressive", "disinterested")
    quiet: tuple[str, ...] = ("unknown",)
    tick_hz: float = 4.0
    live_hz: float = 1.0

    @classmethod
    def from_config(cls, cfg: dict) -> "StateConfig":
        src = (cfg.get("web") or {}).get("state") or {}
        known = {f.name for f in fields(cls)}
        kw = {k: (tuple(v) if isinstance(v, list) else v) for k, v in src.items() if k in known}
        return cls(**kw)

    def cooldown_for(self, emotion: str) -> float:
        return float(self.cooldowns.get(emotion, self.cooldown_s))


def rules_reason(rules: RulesLabel) -> str:
    top = sorted(((s, e) for e, s in rules.scores.items() if e != "unknown"), reverse=True)[:2]
    head = f"{rules.emotion} {rules.scores.get(rules.emotion, rules.confidence):.2f}"
    rest = [f"{e} {s:.2f}" for s, e in top if e != rules.emotion][:1]
    return "Rules: " + ", ".join([head, *rest])


def combine(rules: RulesLabel | None, llm: LLMResult | None, *, now: float, dog_absent_s: float,
            cfg: StateConfig) -> EmotionState:
    if dog_absent_s > cfg.no_dog_unknown_s:
        return EmotionState(ts=now, emotion="unknown", confidence=1.0, source="rules",
                            reason=f"No dog visible for more than {cfg.no_dog_unknown_s:g} s.")
    if rules is None and llm is None:
        return EmotionState(ts=now, emotion="unknown", confidence=0.0, source="rules", reason="Waiting for data.")
    if llm is None:
        return EmotionState(ts=now, emotion=rules.emotion, confidence=rules.confidence, source="rules",
                            reason=rules_reason(rules))
    if rules is None or (llm.emotion != rules.emotion and llm.confidence >= cfg.llm_override_conf):
        return EmotionState(ts=now, emotion=llm.emotion, confidence=llm.confidence, source="llm", reason=llm.reason)
    if llm.emotion == rules.emotion:
        return EmotionState(ts=now, emotion=rules.emotion, confidence=(rules.confidence + llm.confidence) / 2,
                            source="fused", reason=llm.reason)
    return EmotionState(ts=now, emotion=rules.emotion, confidence=rules.confidence, source="rules",
                        reason=rules_reason(rules))


@dataclass
class Span:
    emotion: str
    start: float
    end: float
    confidence: float
    reason: str
    source: str


@dataclass
class Tick:
    live: EmotionState
    current: EmotionState
    changed: EmotionState | None
    notify: EmotionState | None


class FusionState:
    def __init__(self, cfg: StateConfig) -> None:
        self.cfg = cfg
        self._rules: RulesLabel | None = None
        self._llm: LLMResult | None = None
        self._last_dog_ts: float | None = None
        self._any_frame = False
        self._current: EmotionState | None = None
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._last_notified: dict[str, float] = {}
        self._spans: list[Span] = []

    def on_frame(self, ev: FrameEvent) -> None:
        self._any_frame = True
        if ev.dog_detected:
            self._last_dog_ts = ev.ts

    def on_rules(self, r: RulesLabel) -> None:
        self._rules = r

    def on_llm(self, r: LLMResult) -> None:
        self._llm = r

    def _dog_absent_s(self, now: float) -> float:
        if not self._any_frame:
            return 0.0  # no frames at all -> "Waiting for data.", not "no dog"
        return math.inf if self._last_dog_ts is None else now - self._last_dog_ts

    def tick(self, now: float) -> Tick:
        llm = self._llm if self._llm and now - self._llm.ts <= self.cfg.llm_stale_s else None
        live = combine(self._rules, llm, now=now, dog_absent_s=self._dog_absent_s(now), cfg=self.cfg)
        changed = None
        if self._current is None:
            self._adopt(live, now)
            return Tick(live, live, live, None)  # first state never notifies
        if live.emotion == self._current.emotion:
            self._candidate = None
            self._current = live.model_copy(update={"ts": self._current.ts})
            self._refresh_span(now, live)
        else:
            if live.emotion != self._candidate:
                self._candidate, self._candidate_since = live.emotion, now
            if now - self._candidate_since >= self.cfg.persist_s - 1e-9:
                changed = live
                self._adopt(live, now)
            else:
                self._spans[-1].end = now
        return Tick(live, self._current, changed, self._decide_notify(now, changed))

    def _adopt(self, s: EmotionState, now: float) -> None:
        if self._spans:
            self._spans[-1].end = now
        self._current, self._candidate = s, None
        self._spans.append(Span(s.emotion, now, now, s.confidence, s.reason, s.source))

    def _refresh_span(self, now: float, s: EmotionState) -> None:
        sp = self._spans[-1]
        sp.end, sp.confidence, sp.reason, sp.source = now, s.confidence, s.reason, s.source

    def _decide_notify(self, now: float, changed: EmotionState | None) -> EmotionState | None:
        cur = self._current
        if cur is None or cur.emotion in self.cfg.quiet:
            return None
        last = self._last_notified.get(cur.emotion)
        if last is not None and now - last < self.cfg.cooldown_for(cur.emotion):
            return None
        if changed is None and cur.emotion not in self.cfg.always_notify:
            return None
        if changed is None and last is None:
            return None  # always-notify repeat only after a first notification
        self._last_notified[cur.emotion] = now
        return cur.model_copy(update={"ts": now})

    def timeline(self) -> list[Span]:
        return [Span(**vars(s)) for s in self._spans]
