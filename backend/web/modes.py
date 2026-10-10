"""Phase 3 viewing modes (T3.1): one place that defines what each mode means.

Ordering is `blackout < privacy < limited < full`. The effective exposure for a viewer is the
minimum of what the station allows (its cap), what the household chose, and what that viewer is
allowed to see. Lowering is always immediate; raising can need the phone's approval.
"""

MODE_ORDER: tuple[str, ...] = ("blackout", "privacy", "limited", "full")
"""Ascending exposure. Index == how much is allowed to leave the phone."""

DEFAULT_PREVIEW: dict[str, int] = {"fps": 2, "max_edge": 320, "quality": 60}
"""Preview encoding defaults for the `policy` downlink (T3.1/T3.4)."""


class ModeError(ValueError):
    """An unknown mode string reached the policy layer. Callers count it as a dropped message."""


def parse_mode(value: object) -> str:
    """Return a valid mode name, or raise ModeError. Never guesses: an unknown mode is an error."""
    if not isinstance(value, str):
        raise ModeError(f"mode must be a string, got {type(value).__name__}")
    mode = value.strip().lower()
    if mode not in MODE_ORDER:
        raise ModeError(f"unknown mode {value!r}")
    return mode


def mode_rank(mode: str) -> int:
    """0 = blackout (least), 3 = full (most)."""
    return MODE_ORDER.index(parse_mode(mode))


def min_mode(*modes: str) -> str:
    """The least-exposed mode of the arguments (the ceiling that actually applies)."""
    if not modes:
        raise ModeError("min_mode needs at least one mode")
    return min((parse_mode(m) for m in modes), key=mode_rank)


def allows_video(mode: str) -> bool:
    """Whether this mode may show any image of the room at all."""
    return mode_rank(mode) >= MODE_ORDER.index("limited")


def allows_events(mode: str) -> bool:
    """Barks and behaviour numbers still flow in `privacy`: keypoints are numbers, not images."""
    return mode_rank(mode) >= MODE_ORDER.index("privacy")


def allows_audio(mode: str) -> bool:
    """Only `full` streams audio (T3.9)."""
    return mode_rank(mode) >= MODE_ORDER.index("full")