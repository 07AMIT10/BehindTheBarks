import numpy as np
import pytest

from backend.vision.keypoint_map import CANONICAL_NAMES
from backend.vision.pose import PoseEstimator

CFG = {"data": {"keypoint_conf_threshold": 0.3, "pose": {"crop_pad": 0.1}}}
FRAME = np.zeros((100, 200, 3), np.uint8)  # h=100, w=200
BODYPARTS = ["nose", "tail_end", "neck_base", "belly_bottom"]


class FakeRunner:
    """Stands in for DeepLabCut's runner: returns queued (K, 3) keypoints in crop pixel coords."""

    def __init__(self):
        self.queue = []
        self.calls = []

    def inference(self, items):
        (image, ctx), = items
        self.calls.append((image.shape[:2], ctx["bboxes"].tolist()))
        pts = self.queue.pop(0) if self.queue else np.zeros((len(BODYPARTS), 3))
        return [{"bodyparts": np.asarray(pts, dtype=float)[None]}]


def make():
    runner = FakeRunner()
    return PoseEstimator(CFG, runner=runner, bodyparts=BODYPARTS, device="cpu"), runner


def test_keypoints_are_mapped_back_to_full_frame_coords():
    pose, runner = make()
    # bbox (100, 40, 140, 90), pad 0.1 -> crop origin (96, 35), crop 48 wide x 60 tall
    runner.queue.append([[10, 20, 0.9], [30, 40, 0.8], [0, 0, 0.0], [0, 0, 0.0]])
    kps = pose.estimate(FRAME, (100, 40, 140, 90))
    assert kps["nose"] == pytest.approx((106, 55, 0.9))
    assert kps["tail_tip"] == pytest.approx((126, 75, 0.8))
    assert runner.calls[0] == ((60, 48), [[0, 0, 48, 60]])  # model gets the crop and a box covering all of it


def test_all_canonical_names_present_and_low_conf_is_none():
    pose, runner = make()
    runner.queue.append([[10, 20, 0.29], [30, 40, 0.8], [0, 0, 0.0], [0, 0, 0.0]])
    kps = pose.estimate(FRAME, (100, 40, 140, 90))
    assert set(kps) == set(CANONICAL_NAMES)
    assert kps["nose"] is None and kps["tail_tip"] is not None


def test_points_far_outside_the_crop_are_dropped_even_when_confident():
    pose, runner = make()
    runner.queue.append([[300, 20, 0.99], [-40, 40, 0.99], [47, 59, 0.9], [0, 0, 0.0]])  # crop is 48x60
    kps = pose.estimate(FRAME, (100, 40, 140, 90))
    assert kps["nose"] is None and kps["tail_tip"] is None
    assert kps["withers"] is not None  # inside the crop, right at its corner


def test_empty_model_output_gives_all_none_without_crashing():
    pose, runner = make()
    runner.inference = lambda items: [{"bodyparts": np.zeros((0, len(BODYPARTS), 3))}]
    assert all(v is None for v in pose.estimate(FRAME, (100, 40, 140, 90)).values())


def test_model_raising_gives_all_none_instead_of_crashing_the_frame():
    pose, runner = make()

    def boom(items):
        raise RuntimeError("model exploded")

    runner.inference = boom
    kps = pose.estimate(FRAME, (100, 40, 140, 90))
    assert set(kps) == set(CANONICAL_NAMES)
    assert all(v is None for v in kps.values())
    # a second failing call must not raise either (no crash from repeated model errors)
    assert all(v is None for v in pose.estimate(FRAME, (100, 40, 140, 90)).values())


def test_degenerate_bbox_gives_all_none_and_skips_the_model():
    pose, runner = make()
    kps = pose.estimate(FRAME, (500, 500, 510, 510))  # outside the frame -> empty crop
    assert all(v is None for v in kps.values()) and runner.calls == []


def test_custom_runner_requires_bodyparts():
    with pytest.raises(ValueError):
        PoseEstimator(CFG, runner=FakeRunner(), device="cpu")


def test_latency_is_recorded():
    pose, _ = make()
    pose.estimate(FRAME, (100, 40, 140, 90))
    assert pose.latency_stats()["n"] == 1
