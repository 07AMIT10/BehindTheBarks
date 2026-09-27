"""Heuristic emotion scoring: features + audio events -> RulesLabel.

    engine = RulesEngine(cfg)                                      # reads `data.rules`
    label = engine.update(frame_event, recent_audio_events, treat_event_recent=False)

Call `update` once per processed frame, in time order. `recent_audio_events` is any list of AudioEvents;
the engine keeps only those from the last `window_s` (~3 s) before the frame. `treat_event_recent` is
set by the dashboard's "treat dropped" button for a few seconds after a press.

How a score is made. Each emotion is a weighted mix of small pieces of evidence, each 0..1 (a soft
version of "is the tail high?", not a hard yes/no). A feature that is null contributes nothing: it is
left out of the mix rather than counted as "no". If less than `min_coverage` of an emotion's weight is
present, the score is scaled down in proportion, so one lonely feature can't max out an emotion.
Sounds and treat events then add a bonus or apply a penalty (`event_bonus`, `event_penalty`).

    emotion       what we look for                                                        sounds / events
    ------------  ----------------------------------------------------------------------  ------------------------------
    happy         tail neutral-to-high, moderate wag (1-3 Hz), mouth open and loose,      a growl wipes it out
                  ears neutral, body not lowered, moderate motion
    excited       fast wag (> 3 Hz), high tail, high motion, ears up, mouth open          yips and barks add; a treat
                                                                                          event adds; a growl halves it
    relaxed       almost no motion, tail neutral, wag slow or absent, ears neutral,       a growl wipes it out; fades
                  body not lowered                                                        once the dog counts as
                                                                                          disinterested
    anxious       low (not tucked) tail, ears back, some body lowering, pacing            whimpers and howls add
                  (lots of motion while outside the feeding zone)
    fearful       tucked tail, ears flat back, strong body lowering, outside the zone     whimpers add
    aggressive    stiff and still: no wag, no motion, ears up, tail high, mouth tense     a growl adds a lot. With no
                                                                                          growl the score is capped at
                                                                                          `aggressive_max_conf_without_growl`
    disinterested in the feeding zone with low motion, continuously, for longer than      a treat event restarts the
                  `disinterested_s`. Scores 0 before that, 0.7 the moment it is crossed,  clock
                  1.0 at twice the threshold. This is a duration rule, not a posture rule.
    unknown       no dog for longer than `no_dog_unknown_s` (score 1.0), or no emotion    -
                  beats the evidence bar `min_score` (the bar is unknown's score)

Body lowering, ears and mouth are the noisiest features, so they carry the smaller weights.
"Ears forward" and "mouth tension" (aggressive) aren't measured; "ears up" and "mouth open" stand in.
"Retreating from the zone" (fearful) is approximated by "currently outside the zone".

The label. The best score wins, but a different label must stay on top for `hysteresis_s` (~1.5 s)
before it replaces the current one, so a noisy frame can't flip the output. `confidence` is the label's
score minus half the runner-up's, so an ambiguous frame is less confident than a clear one. A gap of
no dog shorter than `no_dog_unknown_s` keeps the previous label; longer gives unknown immediately.

Everything tunable is in config.yaml `data.rules`; the defaults below mirror it.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Any, Sequence

from backend.contracts import EMOTIONS, AudioEvent, Features, FrameEvent, RulesLabel

DEFAULTS: dict[str, Any] = {
    "window_s": 3.0,
    "hysteresis_s": 1.5,
    "no_dog_unknown_s": 2.0,
    "disinterested_s": 8.0,
    "disinterested_grace_s": 1.0,
    "disinterested_min_score": 0.7,
    "aggressive_max_conf_without_growl": 0.5,
    "growl_min_score": 0.3,
    "min_score": 0.3,
    "min_coverage": 0.5,
    "thresholds": {
        "tail_high": 0.3,
        "tail_low": -0.3,
        "tail_tucked": -0.6,
        "wag_fast_hz": 3.0,
        "wag_moderate_min_hz": 1.0,
        "motion_high": 0.6,
        "motion_low": 0.15,
        "body_lowering_mild": 0.2,
        "body_lowering_high": 0.5,
        "mouth_open": 0.4,
    },
    "softness": {"tail_height": 0.2, "wag_hz": 1.0, "motion": 0.15, "body_lowering": 0.1, "mouth_open": 0.15},
    "weights": {
        "happy": {"tail_ok": 1.0, "wag_moderate": 1.5, "mouth_open": 1.0, "ears_neutral": 1.0, "not_lowered": 0.5, "motion_moderate": 0.5},
        "excited": {"wag_fast": 2.0, "tail_high": 1.5, "motion_high": 2.0, "ears_up": 0.5, "mouth_open": 0.5},
        "relaxed": {"motion_low": 2.0, "tail_neutral": 1.5, "wag_slow": 1.0, "ears_neutral": 1.0, "not_lowered": 1.0},
        "anxious": {"tail_low": 1.5, "ears_back": 1.0, "lowering_some": 1.0, "pacing": 1.5},
        "fearful": {"tail_tucked": 2.0, "ears_back": 1.5, "lowering_high": 2.0, "away_from_zone": 0.5},
        "aggressive": {"still_tail": 1.0, "motion_low": 1.0, "ears_up": 1.0, "tail_high": 1.0, "mouth_open": 0.5},
    },
    "event_bonus": {
        "excited": {"yip": 0.25, "bark": 0.15, "treat": 0.25},
        "anxious": {"whimper": 0.3, "howl": 0.15},
        "fearful": {"whimper": 0.15},
        "aggressive": {"growl": 0.4},
    },
    "event_penalty": {
        "happy": {"growl": 1.0},
        "relaxed": {"growl": 1.0},
        "excited": {"growl": 0.5},
    },
}

# How well an observed ear position matches a wanted one. Unknown/None ears are null evidence.
_EAR_MATCH = {
    "up": {"up": 1.0, "neutral": 0.3, "back": 0.0},
    "neutral": {"up": 0.6, "neutral": 1.0, "back": 0.0},
    "back": {"up": 0.0, "neutral": 0.0, "back": 1.0},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# Soft comparisons: 0..1 evidence, None in -> None out. `soft` is the half-width of the fuzzy edge:
# evidence is 0.5 at the threshold, 0 at thr - soft and 1 at thr + soft (mirrored for _below).
def _above(x: float | None, thr: float, soft: float) -> float | None:
    return None if x is None else _clamp01(0.5 + (x - thr) / (2.0 * soft))


def _below(x: float | None, thr: float, soft: float) -> float | None:
    return None if x is None else _clamp01(0.5 - (x - thr) / (2.0 * soft))


def _between(x: float | None, lo: float, hi: float, soft: float) -> float | None:
    if x is None:
        return None
    return min(_above(x, lo, soft), _below(x, hi, soft))  # type: ignore[type-var]


def _ears(ear: str | None, want: str) -> float | None:
    return _EAR_MATCH[want].get(ear) if ear else None


@dataclass
class _View:
    """Everything one scoring function may look at, for one frame."""

    f: Features
    sounds: dict[str, float]  # recent audio label -> best score, plus "treat" -> 1.0 when a treat just dropped
    idle_s: float  # how long the dog has been in the zone with low motion


class RulesEngine:
    def __init__(self, cfg: dict):
        self.p: dict[str, Any] = _deep_merge(DEFAULTS, cfg.get("data", {}).get("rules", {}))
        self._t = self.p["thresholds"]
        self._soft = self.p["softness"]
        self._scorers = {
            "happy": self._happy,
            "excited": self._excited,
            "relaxed": self._relaxed,
            "anxious": self._anxious,
            "fearful": self._fearful,
            "aggressive": self._aggressive,
            "disinterested": self._disinterested,
        }
        self.reset()

    def reset(self) -> None:
        """Forget all history (new clip, or the source restarted)."""
        self._current: str | None = None
        self._candidate: str | None = None
        self._candidate_since = 0.0
        self._last_dog_ts: float | None = None
        self._last_label: RulesLabel | None = None
        self._idle_start: float | None = None
        self._idle_ok_ts = 0.0

    # -- public ------------------------------------------------------------------------------

    def update(
        self,
        frame: FrameEvent,
        audio_events: Sequence[AudioEvent] = (),
        treat_event_recent: bool = False,
    ) -> RulesLabel:
        ts = frame.ts
        idle_s = self._update_idle(frame, treat_event_recent)
        if not frame.dog_detected:
            return self._no_dog(ts)
        self._last_dog_ts = ts

        sounds = self._recent_sounds(ts, audio_events)
        if treat_event_recent:
            sounds["treat"] = 1.0
        view = _View(frame.features, sounds, idle_s)

        scores = {e: _clamp01(fn(view)) for e, fn in self._scorers.items()}
        scores["unknown"] = self.p["min_score"]  # the evidence bar: an emotion has to beat it
        raw = max(scores, key=scores.__getitem__)
        return self._emit(ts, self._apply_hysteresis(raw, ts), scores)

    # -- frame-level logic -------------------------------------------------------------------

    def _recent_sounds(self, ts: float, audio_events: Sequence[AudioEvent]) -> dict[str, float]:
        sounds: dict[str, float] = {}
        for e in audio_events:
            if e.ts > ts - self.p["window_s"]:
                sounds[e.label] = max(sounds.get(e.label, 0.0), e.score)
        return sounds

    def _no_dog(self, ts: float) -> RulesLabel:
        gap = math.inf if self._last_dog_ts is None else ts - self._last_dog_ts
        if gap <= self.p["no_dog_unknown_s"] and self._last_label is not None:
            return self._last_label.model_copy(update={"ts": ts})  # brief dropout: hold the last label
        scores = {e: 0.0 for e in EMOTIONS}
        scores["unknown"] = 1.0
        self._current, self._candidate = "unknown", None
        return self._emit(ts, "unknown", scores)

    def _apply_hysteresis(self, raw: str, ts: float) -> str:
        if self._current is None:
            self._current = raw
        elif raw == self._current:
            self._candidate = None
        else:
            if raw != self._candidate:
                self._candidate, self._candidate_since = raw, ts
            if ts - self._candidate_since >= self.p["hysteresis_s"]:
                self._current, self._candidate = raw, None
        return self._current

    def _emit(self, ts: float, emotion: str, scores: dict[str, float]) -> RulesLabel:
        runner_up = max((s for e, s in scores.items() if e != emotion), default=0.0)
        label = RulesLabel(
            ts=ts,
            emotion=emotion,  # type: ignore[arg-type]
            confidence=_clamp01(scores[emotion] - 0.5 * runner_up),
            scores={e: round(scores[e], 4) for e in EMOTIONS},  # type: ignore[misc]
        )
        self._last_label = label
        return label

    def _update_idle(self, frame: FrameEvent, treat: bool) -> float:
        """Seconds the dog has been in the zone with low motion; a short blip doesn't restart the count."""
        ts, f = frame.ts, frame.features
        ok = (
            frame.dog_detected
            and not treat
            and f.in_feeding_zone is True
            and f.motion_energy is not None
            and f.motion_energy < self._t["motion_low"]
        )
        if treat:
            self._idle_start = None
        elif ok:
            if self._idle_start is None:
                self._idle_start = ts
            self._idle_ok_ts = ts
        elif self._idle_start is not None and ts - self._idle_ok_ts > self.p["disinterested_grace_s"]:
            self._idle_start = None
        if self._idle_start is None or ts - self._idle_ok_ts > self.p["disinterested_grace_s"]:
            return 0.0
        return ts - self._idle_start

    # -- scoring helpers ---------------------------------------------------------------------

    def _blend(self, emotion: str, evidence: dict[str, float | None]) -> float:
        """Weighted mean of the non-null evidence, scaled down when too little of the weight is present."""
        w = self.p["weights"][emotion]
        total = sum(w[k] for k in evidence)
        present = {k: v for k, v in evidence.items() if v is not None}
        present_w = sum(w[k] for k in present)
        if total <= 0 or present_w <= 0:
            return 0.0
        mean = sum(w[k] * v for k, v in present.items()) / present_w
        return mean * min(1.0, (present_w / total) / self.p["min_coverage"])

    def _adjust(self, emotion: str, base: float, v: _View) -> float:
        """Apply the sound / treat bonuses and penalties configured for this emotion."""
        score = base + sum(b * v.sounds.get(sig, 0.0) for sig, b in self.p["event_bonus"].get(emotion, {}).items())
        for sig, pen in self.p["event_penalty"].get(emotion, {}).items():
            score *= 1.0 - pen * v.sounds.get(sig, 0.0)
        return _clamp01(score)

    # -- one scoring function per emotion ----------------------------------------------------

    def _happy(self, v: _View) -> float:
        f, t, s = v.f, self._t, self._soft
        base = self._blend("happy", {
            "tail_ok": _above(f.tail_height, t["tail_low"], s["tail_height"]),
            "wag_moderate": _between(f.tail_wag_hz, t["wag_moderate_min_hz"], t["wag_fast_hz"], s["wag_hz"]),
            "mouth_open": _above(f.mouth_open, t["mouth_open"], s["mouth_open"]),
            "ears_neutral": _ears(f.ear_position, "neutral"),
            "not_lowered": _below(f.body_lowering, t["body_lowering_mild"], s["body_lowering"]),
            "motion_moderate": _between(f.motion_energy, t["motion_low"], t["motion_high"], s["motion"]),
        })
        return self._adjust("happy", base, v)

    def _excited(self, v: _View) -> float:
        f, t, s = v.f, self._t, self._soft
        base = self._blend("excited", {
            "wag_fast": _above(f.tail_wag_hz, t["wag_fast_hz"], s["wag_hz"]),
            "tail_high": _above(f.tail_height, t["tail_high"], s["tail_height"]),
            "motion_high": _above(f.motion_energy, t["motion_high"], s["motion"]),
            "ears_up": _ears(f.ear_position, "up"),
            "mouth_open": _above(f.mouth_open, t["mouth_open"], s["mouth_open"]),
        })
        return self._adjust("excited", base, v)

    def _relaxed(self, v: _View) -> float:
        f, t, s = v.f, self._t, self._soft
        base = self._blend("relaxed", {
            "motion_low": _below(f.motion_energy, t["motion_low"], s["motion"]),
            "tail_neutral": _between(f.tail_height, t["tail_low"], t["tail_high"], s["tail_height"]),
            "wag_slow": _below(f.tail_wag_hz, t["wag_moderate_min_hz"], s["wag_hz"]),
            "ears_neutral": _ears(f.ear_position, "neutral"),
            "not_lowered": _below(f.body_lowering, t["body_lowering_mild"], s["body_lowering"]),
        })
        # Idle long enough to be disinterested: that label takes over instead of "relaxed".
        return self._adjust("relaxed", base * (1.0 - self._disinterested(v)), v)

    def _anxious(self, v: _View) -> float:
        f, t, s = v.f, self._t, self._soft
        pacing = None
        if f.in_feeding_zone is not None:
            pacing = 0.0 if f.in_feeding_zone else _above(f.motion_energy, t["motion_high"], s["motion"])
        base = self._blend("anxious", {
            "tail_low": _between(f.tail_height, t["tail_tucked"], t["tail_low"], s["tail_height"]),  # low, not tucked
            "ears_back": _ears(f.ear_position, "back"),
            "lowering_some": _between(f.body_lowering, t["body_lowering_mild"], t["body_lowering_high"], s["body_lowering"]),
            "pacing": pacing,
        })
        return self._adjust("anxious", base, v)

    def _fearful(self, v: _View) -> float:
        f, t, s = v.f, self._t, self._soft
        away = None if f.in_feeding_zone is None else (0.0 if f.in_feeding_zone else 1.0)
        base = self._blend("fearful", {
            "tail_tucked": _below(f.tail_height, t["tail_tucked"], s["tail_height"]),
            "ears_back": _ears(f.ear_position, "back"),
            "lowering_high": _above(f.body_lowering, t["body_lowering_high"], s["body_lowering"]),
            "away_from_zone": away,
        })
        return self._adjust("fearful", base, v)

    def _aggressive(self, v: _View) -> float:
        f, t, s = v.f, self._t, self._soft
        base = self._blend("aggressive", {
            "still_tail": _below(f.tail_wag_hz, t["wag_moderate_min_hz"], s["wag_hz"]),
            "motion_low": _below(f.motion_energy, t["motion_low"], s["motion"]),
            "ears_up": _ears(f.ear_position, "up"),
            "tail_high": _above(f.tail_height, t["tail_high"], s["tail_height"]),
            "mouth_open": _above(f.mouth_open, t["mouth_open"], s["mouth_open"]),
        })
        score = self._adjust("aggressive", base, v)
        if v.sounds.get("growl", 0.0) < self.p["growl_min_score"]:
            score = min(score, self.p["aggressive_max_conf_without_growl"])
        return score

    def _disinterested(self, v: _View) -> float:
        thr = self.p["disinterested_s"]
        if v.idle_s < thr:
            return 0.0
        lo = self.p["disinterested_min_score"]
        return lo + (1.0 - lo) * min(1.0, (v.idle_s - thr) / thr)
