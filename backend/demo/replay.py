"""Accelerated offline replay of a clip's events through the live fusion stack.

Same classes as the live path (FusionState, TriggerPolicy, RulesEngine, LLMInterpreter), but with a
fake clock advanced in fixed ticks and no network/timing involved. Pure apart from the interpreter.
"""

from __future__ import annotations

from typing import Any, Callable

from backend.contracts import AudioEvent, FrameEvent
from backend.fusion.llm_triggers import TriggerPolicy
from backend.fusion.rules import RulesEngine
from backend.fusion.state import FusionState, StateConfig


def _empty_cfg() -> dict:
    return {"web": {"state": {}, "llm": {"min_interval_s": 3.0, "heartbeat_s": 10.0, "audio_trigger_score": 0.6}}}


async def replay_clip(events: list[dict], get_frame: Callable[[float], bytes | None], interpreter: Any,
                      rules_cfg: dict | None = None, tick_hz: float = 4.0, live_hz: float = 1.0) -> dict:
    """events: clip-relative {"t","type","data"} dicts, any order. Returns the timeline.json dict."""
    evs = sorted(events, key=lambda e: e["t"])
    end = max([e["t"] for e in evs] + [0.0])
    rules = RulesEngine(rules_cfg or {})
    fs = FusionState(StateConfig())
    trig = TriggerPolicy.from_config(_empty_cfg())
    feats: list[tuple[float, Any]] = []
    audio: list[AudioEvent] = []
    cur_rules = None
    states, live, llm_out, notes, treats = [], [], [], [], []
    last_live = -1e18

    async def run_llm(reason: str, now: float) -> None:
        res = await interpreter.interpret(trigger=reason, now=now, features=feats[-6:],
                                          audio=[a for a in audio if a.ts >= now - 3.0],
                                          rules=cur_rules, jpeg=get_frame(now))
        if res is not None:
            fs.on_llm(res)
            llm_out.append({"t": now, "emotion": res.emotion, "confidence": res.confidence, "reason": res.reason,
                            "provider": res.provider, "model": res.model, "trigger": res.trigger})

    i, t, dt = 0, 0.0, 1.0 / tick_hz
    while t <= end + 1e-9:
        while i < len(evs) and evs[i]["t"] <= t + 1e-9:
            e = evs[i]
            if e["type"] == "frame":
                f = FrameEvent.model_validate(e["data"])
                fs.on_frame(f)
                if f.dog_detected:
                    feats.append((f.ts, f.features))
                label = rules.update(f, [a for a in audio if a.ts > f.ts - 3.0])
                cur_rules = label
                fs.on_rules(label)
                trig.note_rules(label)
            elif e["type"] == "audio":
                a = AudioEvent.model_validate(e["data"])
                audio.append(a)
                trig.note_audio(a)
            elif e["type"] == "treat":
                trig.note_treat()
                treats.append(round(e["t"], 3))
            i += 1
        tick = fs.tick(t)
        if tick.changed is not None:
            states.append({"t": t, "emotion": tick.changed.emotion, "confidence": tick.changed.confidence,
                           "source": tick.changed.source, "reason": tick.changed.reason})
        elif t - last_live >= 1.0 / live_hz - 1e-9:
            c = tick.current
            live.append({"t": t, "emotion": c.emotion, "confidence": c.confidence, "source": c.source,
                         "reason": c.reason})
            last_live = t
        if tick.notify is not None:
            notes.append({"t": t, "emotion": tick.notify.emotion, "reason": tick.notify.reason})
        if interpreter.enabled:
            reason = trig.due(t, False)
            if reason:
                await run_llm(reason, t)  # offline: inline, so the result feeds the next tick
        t = round(t + dt, 6)
    if not states and live:
        first = live[0]  # first state adopted immediately: mirror it as a change at t=0
        states.append({"t": 0.0, **{k: first[k] for k in ("emotion", "confidence", "source", "reason")}})
    return {"duration_s": end, "states": states, "live": live, "llm": llm_out,
            "notifications": notes, "treats": sorted(treats)}
