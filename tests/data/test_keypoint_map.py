from backend.vision.keypoint_map import CANONICAL_NAMES, SKELETON, SUPERANIMAL_TO_CANONICAL, to_canonical


def test_every_canonical_name_is_always_present():
    out = to_canonical({})
    assert set(out) == set(CANONICAL_NAMES)
    assert all(v is None for v in out.values())


def test_renames_and_keeps_confident_points():
    out = to_canonical({"tail_end": (10.0, 20.0, 0.9), "neck_base": (1.0, 2.0, 0.5), "back_end": (3.0, 4.0, 0.4)})
    assert out["tail_tip"] == (10.0, 20.0, 0.9)
    assert out["withers"] == (1.0, 2.0, 0.5)
    assert out["hip"] == (3.0, 4.0, 0.4)


def test_below_threshold_is_none_not_a_guess():
    out = to_canonical({"tail_end": (10.0, 20.0, 0.29), "nose": (5.0, 5.0, 0.3)}, conf_thr=0.3)
    assert out["tail_tip"] is None
    assert out["nose"] == (5.0, 5.0, 0.3)  # exactly at the threshold is kept


def test_unmapped_raw_names_are_ignored():
    out = to_canonical({"right_antler_end": (1.0, 1.0, 0.99), "belly_bottom": (2.0, 2.0, 0.99)})
    assert all(v is None for v in out.values())


def test_non_finite_coordinates_are_dropped():
    out = to_canonical({"nose": (float("nan"), 1.0, 0.9), "tail_end": (1.0, float("inf"), 0.9)})
    assert out["nose"] is None and out["tail_tip"] is None


def test_mapping_targets_and_skeleton_use_canonical_names_only():
    assert set(SUPERANIMAL_TO_CANONICAL.values()) == set(CANONICAL_NAMES)
    assert len(set(SUPERANIMAL_TO_CANONICAL.values())) == len(SUPERANIMAL_TO_CANONICAL)  # no two raw names collide
    assert all(a in CANONICAL_NAMES and b in CANONICAL_NAMES for a, b in SKELETON)
