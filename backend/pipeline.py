"""The handoff interface between Data and Web.

Web constructs a Pipeline, awaits run() with three callbacks, and never imports anything else from
backend/vision/ or backend/audio/. Implemented in Step 10; signatures are frozen now.
"""

from __future__ import annotations

from typing import Any, Callable

from backend.contracts import AudioEvent, FrameEvent, RulesLabel

FrameCallback = Callable[[FrameEvent], None]
AudioCallback = Callable[[AudioEvent], None]
RulesCallback = Callable[[RulesLabel], None]

PIPELINE_METHODS: tuple[str, ...] = (
    "run", "latest_frame_jpeg", "mark_treat", "ingest_frame", "ingest_audio", "status", "stop",
)


class Pipeline:
    def __init__(self, config: dict[str, Any]) -> None:
        """Build the pipeline from the parsed config.yaml (the full dict; reads its `data` section).

        Loading models is done here, not in run(), so a slow start doesn't delay the first event.
        """
        raise NotImplementedError

    async def run(
        self,
        on_frame_event: FrameCallback,
        on_audio_event: AudioCallback,
        on_rules_label: RulesCallback,
    ) -> None:
        """Process the configured source until it ends or stop() is called.

        Callbacks are invoked on the caller's event loop thread and must return quickly.
        - on_frame_event(FrameEvent): ~5-10 per second, one per processed video frame, including
          frames with no dog (dog_detected=False).
        - on_audio_event(AudioEvent): one per YAMNet window, ~every 0.48 s (0.96 s window, 50% hop).
        - on_rules_label(RulesLabel): once per processed frame, after hysteresis. Its .scores holds
          a 0..1 score for every emotion in the fixed vocabulary.
        The video loop never waits on the callbacks; frames are dropped rather than queued.
        """
        raise NotImplementedError

    def latest_frame_jpeg(self) -> bytes | None:
        """Newest raw frame as JPEG (no overlay drawn), or None before the first frame."""
        raise NotImplementedError

    def mark_treat(self, ts: float) -> None:
        """Treat button: set the rules engine's treat_event_recent for data.rules.treat_window_s."""
        raise NotImplementedError

    def ingest_frame(self, jpeg: bytes, ts: float) -> None:
        """Phone camera frame (browser source). Non-blocking, never raises, drops oldest when behind;
        a no-op with one warning unless the source type is `browser`."""
        raise NotImplementedError

    def ingest_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None:
        """Phone mic chunk: mono Int16 LE PCM. Same rules as ingest_frame."""
        raise NotImplementedError

    def status(self) -> dict:
        """{"source", "state": "running"|"stalled"|"stopped", "fps", "last_frame_age_s", "audio_ok"}."""
        raise NotImplementedError

    def stop(self) -> None:
        """Stop all threads and release camera/microphone. Safe to call more than once."""
        raise NotImplementedError
