"""Daily Doggy Diary & Emotional Wellbeing Digest.
Aggregates continuous fused emotions, vision features, audio barks, and treats into 4 behavior buckets:
  1. Rest & Sleep
  2. Active Play & Energy
  3. Alertness & Guarding
  4. Routine & Feeding
Computes Daily Canine Happiness Score % and highlights the pet's best moments of the day.
"""

from __future__ import annotations

import datetime
import math
from typing import Any

from backend.contracts import Emotion


def mood_label_for_score(score: int) -> str:
    if score >= 90:
        return "Thriving & Blissful"
    if score >= 80:
        return "Content & Joyful"
    if score >= 70:
        return "Calm & Settled"
    if score >= 55:
        return "Mildly Restless"
    if score >= 40:
        return "Noticeably Anxious"
    return "Stressed / High Alert"


def build_daily_diary(
    spans: list[dict[str, Any]],
    treats: list[float] | None = None,
    audio_events: list[dict[str, Any]] | None = None,
    dog_name: str = "Your dog",
    target_date: str | None = None,
) -> dict[str, Any]:
    """Aggregate emotion spans and events into a daily wellbeing digest.

    spans: list of dicts with keys: start, end, emotion, confidence, reason, snapshot
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    date_str = target_date or now.strftime("%Y-%m-%d")

    treat_times = treats or []
    audio_list = audio_events or []

    rest_seconds = 0.0
    play_seconds = 0.0
    alert_seconds = 0.0
    routine_seconds = len(treat_times) * 120.0  # ~2 mins per treat interaction

    longest_rest_span: dict[str, Any] | None = None
    max_rest_duration = 0.0

    peak_play_span: dict[str, Any] | None = None
    max_play_duration = 0.0

    peak_alert_span: dict[str, Any] | None = None
    max_alert_duration = 0.0

    for s in spans:
        start = float(s.get("start", 0.0))
        end = float(s.get("end", start))
        dur = max(0.0, end - start)
        emotion = str(s.get("emotion", "unknown"))

        if emotion in ("relaxed", "disinterested"):
            rest_seconds += dur
            if dur > max_rest_duration:
                max_rest_duration = dur
                longest_rest_span = s
        elif emotion in ("happy", "excited"):
            play_seconds += dur
            if dur > max_play_duration:
                max_play_duration = dur
                peak_play_span = s
        elif emotion in ("anxious", "fearful", "aggressive"):
            alert_seconds += dur
            if dur > max_alert_duration:
                max_alert_duration = dur
                peak_alert_span = s
        else:
            # Default unknown / baseline distributes gently into calm rest
            rest_seconds += dur * 0.5

    total_monitored_seconds = rest_seconds + play_seconds + alert_seconds + routine_seconds
    total_monitored_minutes = int(math.ceil(total_monitored_seconds / 60.0))

    if total_monitored_seconds > 0:
        raw_score = ((rest_seconds * 0.95 + play_seconds * 1.0) / total_monitored_seconds) * 100.0
        # Mild penalty for long alert states
        alert_penalty = min(30.0, (alert_seconds / total_monitored_seconds) * 50.0)
        happiness_score = int(round(max(10.0, min(100.0, raw_score - alert_penalty))))
    else:
        happiness_score = 100  # Default peaceful state

    mood_label = mood_label_for_score(happiness_score)

    rest_mins = int(round(rest_seconds / 60.0))
    play_mins = int(round(play_seconds / 60.0))
    alert_mins = int(round(alert_seconds / 60.0))
    routine_mins = int(round(routine_seconds / 60.0))

    total_mins = max(1, rest_mins + play_mins + alert_mins + routine_mins)
    percentages = {
        "rest": int(round((rest_mins / total_mins) * 100)),
        "play": int(round((play_mins / total_mins) * 100)),
        "alert": int(round((alert_mins / total_mins) * 100)),
        "routine": int(round((routine_mins / total_mins) * 100)),
    }

    # Build Highlights
    highlights: list[dict[str, Any]] = []

    if longest_rest_span and max_rest_duration >= 60.0:
        start_dt = datetime.datetime.fromtimestamp(longest_rest_span["start"], tz=datetime.timezone.utc)
        highlights.append({
            "kind": "rest",
            "title": "Deep Restful Nap",
            "time": start_dt.strftime("%H:%M"),
            "duration_mins": int(round(max_rest_duration / 60.0)),
            "reason": longest_rest_span.get("reason", "Peaceful rest"),
            "snapshot": longest_rest_span.get("snapshot"),
        })

    if peak_play_span and max_play_duration >= 30.0:
        start_dt = datetime.datetime.fromtimestamp(peak_play_span["start"], tz=datetime.timezone.utc)
        highlights.append({
            "kind": "play",
            "title": "Happy & Energetic Burst",
            "time": start_dt.strftime("%H:%M"),
            "duration_mins": int(round(max_play_duration / 60.0)),
            "reason": peak_play_span.get("reason", "Wagging tail and cheerful bounding"),
            "snapshot": peak_play_span.get("snapshot"),
        })

    if peak_alert_span and max_alert_duration >= 20.0:
        start_dt = datetime.datetime.fromtimestamp(peak_alert_span["start"], tz=datetime.timezone.utc)
        highlights.append({
            "kind": "alert",
            "title": "Watchful Alertness",
            "time": start_dt.strftime("%H:%M"),
            "duration_mins": int(round(max_alert_duration / 60.0)),
            "reason": peak_alert_span.get("reason", "Alert posture / bark detected"),
            "snapshot": peak_alert_span.get("snapshot"),
        })

    if treat_times:
        first_t = datetime.datetime.fromtimestamp(treat_times[0], tz=datetime.timezone.utc)
        highlights.append({
            "kind": "routine",
            "title": f"Treat Time ({len(treat_times)} dropped)",
            "time": first_t.strftime("%H:%M"),
            "duration_mins": len(treat_times) * 2,
            "reason": "Yummy reward enjoyed by dog",
            "snapshot": None,
        })

    # Summary narrative
    if happiness_score >= 80:
        summary = f"{dog_name} had a wonderful day with {rest_mins}m of restful snooze and {play_mins}m of playful joy."
    elif happiness_score >= 60:
        summary = f"{dog_name} had a mostly calm day with a few brief alert moments ({alert_mins}m)."
    else:
        summary = f"{dog_name} showed signs of restlessness today with {alert_mins}m on alert. Check in with attention cues."

    return {
        "ok": True,
        "date": date_str,
        "dog_name": dog_name,
        "happiness_score": happiness_score,
        "mood_label": mood_label,
        "total_monitored_minutes": total_monitored_minutes,
        "summary": summary,
        "buckets": {
            "rest_minutes": rest_mins,
            "play_minutes": play_mins,
            "alert_minutes": alert_mins,
            "routine_minutes": routine_mins,
        },
        "percentages": percentages,
        "highlights": highlights,
    }
