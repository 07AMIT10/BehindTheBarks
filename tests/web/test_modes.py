"""T3.1 mode ordering: the ceiling maths every other mode behaviour depends on."""

import pytest

from backend.web.modes import (DEFAULT_PREVIEW, MODE_ORDER, ModeError, allows_audio, allows_events,
                               allows_video, min_mode, mode_rank, parse_mode)


def test_ordering_is_blackout_to_full():
    assert MODE_ORDER == ("blackout", "privacy", "limited", "full")
    assert mode_rank("blackout") < mode_rank("privacy") < mode_rank("limited") < mode_rank("full")


def test_parse_mode_accepts_known_and_normalises():
    assert parse_mode("full") == "full"
    assert parse_mode("  LIMITED ") == "limited"
    assert parse_mode("Privacy") == "privacy"


@pytest.mark.parametrize("bad", ["ultra", "", "FULLER", None, 7, ["full"]])
def test_parse_mode_rejects_unknown_values(bad):
    with pytest.raises(ModeError):
        parse_mode(bad)


def test_min_mode_picks_the_least_exposed():
    assert min_mode("full", "privacy") == "privacy"
    assert min_mode("limited", "blackout", "full") == "blackout"
    assert min_mode("full", "full") == "full"
    # Order of arguments must not matter.
    assert min_mode("privacy", "full") == min_mode("full", "privacy")


def test_min_mode_needs_at_least_one_mode():
    with pytest.raises(ModeError):
        min_mode()


def test_min_mode_propagates_unknown_modes():
    with pytest.raises(ModeError):
        min_mode("full", "nope")


def test_capabilities_per_mode():
    assert not allows_video("privacy") and not allows_video("blackout")
    assert allows_video("limited") and allows_video("full")
    assert allows_events("privacy") and allows_events("limited") and allows_events("full")
    assert not allows_events("blackout")
    assert allows_audio("full")
    assert not allows_audio("limited") and not allows_audio("privacy") and not allows_audio("blackout")


def test_default_preview_shape():
    assert DEFAULT_PREVIEW == {"fps": 2, "max_edge": 320, "quality": 60}