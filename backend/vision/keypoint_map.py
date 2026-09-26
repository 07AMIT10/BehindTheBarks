"""Maps model-specific keypoint names to our canonical names.

Downstream code (face.py, features.py, the dashboard overlay) only ever sees CANONICAL_NAMES, so
swapping the pose model means writing one new mapping here and nothing else.

SuperAnimal-Quadruped has 39 points; we keep the 17 we need. Two choices worth knowing about:
  * withers <- neck_base, hip <- back_end. Checked by eye on stock dog footage: neck_base sits on the
    shoulders and was the more confident of the two withers candidates (back_base), back_end sits at the
    rump.
  * "left" and "right" are the model's labels, taken as given. Nothing downstream depends on which
    side of the image they fall on.
"""

from __future__ import annotations

from typing import Mapping, Sequence

Keypoint = tuple[float, float, float]  # x, y, confidence

CANONICAL_NAMES: tuple[str, ...] = (
    "nose",
    "upper_jaw",
    "lower_jaw",
    "mouth_left",
    "mouth_right",
    "left_eye",
    "right_eye",
    "left_ear_base",
    "left_ear_tip",
    "right_ear_base",
    "right_ear_tip",
    "withers",
    "hip",
    "tail_base",
    "tail_tip",
    "left_front_paw",
    "right_front_paw",
    "left_back_paw",
    "right_back_paw",
)

SUPERANIMAL_TO_CANONICAL: dict[str, str] = {
    "nose": "nose",
    "upper_jaw": "upper_jaw",
    "lower_jaw": "lower_jaw",
    "mouth_end_left": "mouth_left",
    "mouth_end_right": "mouth_right",
    "left_eye": "left_eye",
    "right_eye": "right_eye",
    "left_earbase": "left_ear_base",
    "left_earend": "left_ear_tip",
    "right_earbase": "right_ear_base",
    "right_earend": "right_ear_tip",
    "neck_base": "withers",
    "back_end": "hip",
    "tail_base": "tail_base",
    "tail_end": "tail_tip",
    "front_left_paw": "left_front_paw",
    "front_right_paw": "right_front_paw",
    "back_left_paw": "left_back_paw",
    "back_right_paw": "right_back_paw",
}

# Edges for drawing a skeleton overlay (canonical names).
SKELETON: tuple[tuple[str, str], ...] = (
    ("nose", "upper_jaw"),
    ("upper_jaw", "lower_jaw"),
    ("left_eye", "nose"),
    ("right_eye", "nose"),
    ("left_ear_base", "left_ear_tip"),
    ("right_ear_base", "right_ear_tip"),
    ("nose", "withers"),
    ("withers", "hip"),
    ("hip", "tail_base"),
    ("tail_base", "tail_tip"),
    ("withers", "left_front_paw"),
    ("withers", "right_front_paw"),
    ("hip", "left_back_paw"),
    ("hip", "right_back_paw"),
)


def to_canonical(
    raw: Mapping[str, Sequence[float]],
    conf_thr: float = 0.3,
    mapping: Mapping[str, str] = SUPERANIMAL_TO_CANONICAL,
) -> dict[str, Keypoint | None]:
    """Rename raw model keypoints ({name: (x, y, conf)}) to canonical names.

    Every canonical name is present in the result. A point that is missing from `raw`, has non-finite
    coordinates or falls below `conf_thr` becomes None: we never guess a position. Raw names that are
    not in `mapping` are ignored.
    """
    out: dict[str, Keypoint | None] = {name: None for name in CANONICAL_NAMES}
    for raw_name, canon in mapping.items():
        p = raw.get(raw_name)
        if p is None:
            continue
        x, y, c = (float(v) for v in p[:3])
        if c >= conf_thr and x == x and y == y and abs(x) != float("inf") and abs(y) != float("inf"):
            out[canon] = (x, y, c)
    return out
