"""MockPipeline: same interface as backend.pipeline.Pipeline, scripted fake data (Web builds against it).

90 s loop: relaxed -> excited (treat) -> happy (eating) -> disinterested -> anxious -> absent -> relaxed.
Deterministic for a given clock (seeded RNG per step time), so tests can assert on it.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Callable

import cv2
import numpy as np

from backend.contracts import EMOTIONS, AudioEvent, Features, FrameEvent, RulesLabel

PHASES: list[tuple[str, float]] = [
    ("relaxed", 12.0), ("excited", 12.0), ("happy", 18.0), ("disinterested", 14.0),
    ("anxious", 10.0), ("absent", 6.0), ("relaxed", 18.0),
]
LOOP_S = sum(d for _, d in PHASES)
_EXCITED_START = 12.0
W, H = 640, 480

# per phase: tail_height, wag_hz, ear, mouth_open, body_lowering, motion, in_zone, (audio label, every s)
_PROFILE: dict[str, dict[str, Any]] = {
    "relaxed":       dict(tail=0.0, wag=0.5, ear="neutral", mouth=0.3, low=0.05, motion=0.08, zone=True, sound=None),
    "excited":       dict(tail=0.7, wag=4.5, ear="up", mouth=0.7, low=0.0, motion=0.85, zone=True, sound=("yip", 1.5)),
    "happy":         dict(tail=0.3, wag=2.0, ear="neutral", mouth=0.55, low=0.2, motion=0.35, zone=True, sound=None),
    "disinterested": dict(tail=-0.1, wag=0.0, ear="neutral", mouth=0.1, low=0.1, motion=0.03, zone=True, sound=None),
    "anxious":       dict(tail=-0.45, wag=0.3, ear="back", mouth=0.2, low=0.35, motion=0.7, zone=False, sound=("whimper", 2.0)),
}
_SKELETON = (("nose", "withers"), ("withers", "hip"), ("hip", "tail_base"), ("tail_base", "tail_tip"),
             ("withers", "left_front_paw"), ("withers", "right_front_paw"),
             ("hip", "left_back_paw"), ("hip", "right_back_paw"),
             ("nose", "left_ear_tip"), ("nose", "right_ear_tip"))


class MockPipeline:
    def __init__(self, config: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._fps = float((config.get("data") or {}).get("fps", 8))
        self._t0 = clock()
        self._running = False
        self._halt = False
        self._jpeg: bytes | None = None
        self._phone_jpeg: bytes | None = None
        self._phone_ts: float | None = None
        self._phone_audio_ts: float | None = None
        self._last_frame_ts: float | None = None
        self._last_sound: dict[str, float] = {}

    # -- script -------------------------------------------------------------------------------
    def _script_t(self, now: float) -> float:
        return (now - self._t0) % LOOP_S

    def phase_at(self, now: float) -> str:
        t = self._script_t(now)
        for name, dur in PHASES:
            if t < dur:
                return name
            t -= dur
        return PHASES[-1][0]

    def step(self, now: float) -> tuple[FrameEvent, list[AudioEvent], RulesLabel]:
        phase = self.phase_at(now)
        rng = np.random.default_rng(int(now * 1000) & 0xFFFFFFFF)
        if phase == "absent":
            frame = FrameEvent(ts=now, source="mock", dog_detected=False)
            rules = RulesLabel(ts=now, emotion="unknown", confidence=1.0,
                               scores={e: (1.0 if e == "unknown" else 0.0) for e in EMOTIONS})
            audio: list[AudioEvent] = []
        else:
            p = _PROFILE[phase]
            frame = self._frame(now, p, rng)
            audio = self._audio(now, p, rng)
            rules = self._rules(now, phase, rng)
        self._last_frame_ts = now
        self._jpeg = self._phone_jpeg if self._phone_fresh(now) else self._render(frame)
        return frame, audio, rules

    def _frame(self, now: float, p: dict, rng) -> FrameEvent:
        jitter = lambda s: float(rng.normal(0, s))  # noqa: E731
        maybe = lambda v: None if rng.random() < 0.08 else v  # noqa: E731
        f = Features(
            tail_height=float(np.clip(p["tail"] + jitter(0.05), -1, 1)),
            tail_wag_hz=maybe(max(0.0, p["wag"] + jitter(0.2))),
            ear_position=maybe(p["ear"]),
            mouth_open=maybe(float(np.clip(p["mouth"] + jitter(0.05), 0, 1))),
            body_lowering=float(np.clip(p["low"] + jitter(0.03), 0, 1)),
            motion_energy=float(np.clip(p["motion"] + jitter(0.05), 0, 1)),
            in_feeding_zone=p["zone"],
        )
        cx = W / 2 + (120 * math.sin(now * 0.8) if p["motion"] > 0.5 else 10 * math.sin(now * 0.3))
        cy = H * 0.62 + p["low"] * 60
        wag = 25 * math.sin(2 * math.pi * p["wag"] * now)
        kp = {
            "nose": (cx - 120, cy - 60), "left_ear_tip": (cx - 105, cy - 95), "right_ear_tip": (cx - 95, cy - 92),
            "withers": (cx - 60, cy - 30), "hip": (cx + 60, cy - 30), "tail_base": (cx + 75, cy - 35),
            "tail_tip": (cx + 120, cy - 35 - 60 * p["tail"] + wag),
            "left_front_paw": (cx - 65, cy + 50), "right_front_paw": (cx - 50, cy + 52),
            "left_back_paw": (cx + 55, cy + 50), "right_back_paw": (cx + 70, cy + 52),
        }
        body = {k: (float(x), float(y), float(np.clip(0.85 + jitter(0.05), 0, 1))) for k, (x, y) in kp.items()}
        bbox = (cx - 140, cy - 110, cx + 140, cy + 60)
        return FrameEvent(ts=now, source="mock", dog_detected=True, bbox=bbox, bbox_conf=0.9,
                          body_keypoints=body, face_landmarks=None, features=f)

    def _audio(self, now: float, p: dict, rng) -> list[AudioEvent]:
        if not p["sound"]:
            return []
        label, every = p["sound"]
        last = self._last_sound.get(label)
        if last is not None and 0 <= now - last < every:
            return []
        self._last_sound[label] = now
        return [AudioEvent(ts=now, label=label, score=float(np.clip(0.8 + rng.normal(0, 0.05), 0, 1)))]

    def _rules(self, now: float, phase: str, rng) -> RulesLabel:
        scores = {e: float(np.clip(rng.uniform(0.05, 0.3), 0, 1)) for e in EMOTIONS}
        scores[phase] = float(np.clip(0.75 + rng.normal(0, 0.05), 0, 1))
        scores["unknown"] = 0.3
        runner = max(s for e, s in scores.items() if e != phase)
        return RulesLabel(ts=now, emotion=phase, confidence=float(np.clip(scores[phase] - 0.5 * runner, 0, 1)),
                          scores=scores)

    def _render(self, frame: FrameEvent) -> bytes:
        img = np.full((H, W, 3), 60, np.uint8)
        cv2.putText(img, "MOCK", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
        kp = frame.body_keypoints
        for a, b in _SKELETON:
            if kp.get(a) and kp.get(b):
                cv2.line(img, tuple(map(int, kp[a][:2])), tuple(map(int, kp[b][:2])), (240, 240, 240), 3)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else (self._jpeg or b"")

    def _phone_fresh(self, now: float) -> bool:
        return self._phone_jpeg is not None and self._phone_ts is not None and now - self._phone_ts <= 2.0

    # -- Pipeline interface -------------------------------------------------------------------
    async def run(self, on_frame_event, on_audio_event, on_rules_label) -> None:
        self._running, self._halt = True, False
        period = 1.0 / self._fps
        try:
            while not self._halt:
                frame, audio, rules = self.step(self._clock())
                on_frame_event(frame)
                for a in audio:
                    on_audio_event(a)
                on_rules_label(rules)
                await asyncio.sleep(period)
        finally:
            self._running = False

    def latest_frame_jpeg(self) -> bytes | None:
        return self._jpeg

    def mark_treat(self, ts: float) -> None:
        self._t0 = ts - _EXCITED_START

    def ingest_frame(self, jpeg: bytes, ts: float) -> None:
        if jpeg[:2] == b"\xff\xd8":  # JPEG SOI; anything else is ignored, never raised
            self._phone_jpeg, self._phone_ts = bytes(jpeg), ts

    def ingest_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None:
        self._phone_audio_ts = ts

    def status(self) -> dict:
        now = self._clock()
        age = None if self._last_frame_ts is None else max(0.0, now - self._last_frame_ts)
        state = "running" if self._running else ("stopped" if self._halt else "running")
        return {"source": "browser" if self._phone_jpeg else "mock", "state": state, "fps": self._fps,
                "last_frame_age_s": age,
                "audio_ok": self._phone_audio_ts is not None and now - self._phone_audio_ts <= 2.0}

    def stop(self) -> None:
        self._halt = True
