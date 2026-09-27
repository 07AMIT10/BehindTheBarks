import numpy as np
import pytest

from backend.vision import face as F
from backend.vision.face import FaceLandmarker
from backend.vision.keypoint_map import CANONICAL_NAMES

CFG = {"data": {"face": {"crop_pad": 0.0, "min_crop_px": 20, "min_head_points": 3}}}
FRAME = np.full((200, 300, 3), 128, np.uint8)


def kps_with(**points):
    kps = {n: None for n in CANONICAL_NAMES}
    kps.update({k: (v[0], v[1], 0.9) for k, v in points.items()})
    return kps


HEAD = kps_with(nose=(100, 100), left_eye=(120, 60), right_eye=(160, 60), left_ear_base=(110, 40))  # 60 x 60 box


class FakeRunner:
    def __init__(self, out=None):
        self.out = out
        self.calls = []

    def __call__(self, x):
        self.calls.append(x)
        if self.out is not None:
            return self.out
        # a spread-out grid of points well inside the crop
        g = np.linspace(0.2, 0.8, F.N_LANDMARKS)
        return np.stack([g, g[::-1]], axis=1).reshape(-1)


def make(out=None, **face_cfg):
    runner = FakeRunner(out)
    cfg = {"data": {"face": {**CFG["data"]["face"], **face_cfg}}}
    return FaceLandmarker(cfg, runner=runner), runner


def test_constants_are_46_unique_in_range_indices():
    grouped = [i for idxs in F.GROUPS.values() for i in idxs]
    assert len(grouped) == len(set(grouped))
    assert all(0 <= i < F.N_LANDMARKS for i in grouped)
    named = [v for k, v in vars(F).items() if k.isupper() and isinstance(v, int) and k not in ("N_LANDMARKS", "INPUT_SIZE")]
    assert all(0 <= i < F.N_LANDMARKS for i in named)


def test_landmarks_are_mapped_back_to_full_frame_pixels():
    out = np.full(92, 0.5)
    out[2 * F.NOSE_UPPER: 2 * F.NOSE_UPPER + 2] = [0.0, 1.0]  # top-left x, bottom y of the crop
    face, runner = make(out)
    lms = face.estimate(FRAME, HEAD)
    # head box: x 100..160, y 40..100 -> square side 60 at (100, 40)
    assert len(lms) == F.N_LANDMARKS
    assert lms[F.NOSE_UPPER] == pytest.approx([100, 100])
    assert lms[F.CHIN] == pytest.approx([130, 70])  # crop centre
    assert runner.calls[0].shape == (1, F.INPUT_SIZE, F.INPUT_SIZE, 3)


def test_model_gets_rgb_scaled_to_unit_range():
    frame = np.zeros((200, 300, 3), np.uint8)
    frame[..., 2] = 255  # BGR red
    face, runner = make()
    face.estimate(frame, HEAD)
    x = runner.calls[0]
    assert x.dtype == np.float32 and x.max() <= 1.0
    assert x[0, 192, 192, 0] == pytest.approx(1.0) and x[0, 192, 192, 2] == pytest.approx(0.0)  # R first


def test_head_box_pads_and_squares():
    face, _ = make(crop_pad=0.5)
    x1, y1, side = face.head_box(HEAD)
    assert side == pytest.approx(120) and (x1, y1) == pytest.approx((70, 10))


def test_head_partly_outside_frame_keeps_the_mapping():
    kps = kps_with(nose=(5, 10), left_eye=(25, -10), right_eye=(45, 10), left_ear_base=(5, -20))
    out = np.linspace(0.2, 0.8, 92)
    out[0:2] = [0.5, 0.5]  # landmark 0 at the crop centre
    face, _ = make(out)
    lms = face.estimate(FRAME, kps)
    assert lms[0] == pytest.approx([25, -5])  # crop centre lies where the head box centre is, even off-frame


@pytest.mark.parametrize(
    "kps",
    [
        kps_with(),  # nothing
        kps_with(nose=(100, 100)),  # a lone nose
        kps_with(nose=(100, 100), upper_jaw=(105, 110), lower_jaw=(110, 120)),  # 3 points but no eye or ear base
        kps_with(nose=(100, 100), left_eye=(105, 95), right_eye=(108, 95)),  # 8 px head: too small
    ],
)
def test_unusable_head_gives_none_without_running_the_model(kps):
    face, runner = make()
    assert face.estimate(FRAME, kps) is None
    assert runner.calls == []


def test_landmarks_mostly_outside_the_crop_are_rejected():
    out = np.full(92, 0.5)
    out[:20] = 3.0  # 10 of 46 landmarks far outside: 78% inside < 90%
    face, _ = make(out)
    assert face.estimate(FRAME, HEAD) is None


def test_collapsed_landmarks_are_rejected():
    face, _ = make(np.full(92, 0.5))
    face.min_spread = 0.25
    assert face.estimate(FRAME, HEAD) is None  # all points identical: extent 0


def test_non_finite_or_wrong_size_output_is_rejected():
    bad = np.full(92, 0.5)
    bad[3] = np.nan
    assert make(bad)[0].estimate(FRAME, HEAD) is None
    assert make(np.full(90, 0.5))[0].estimate(FRAME, HEAD) is None


def test_model_raising_gives_none_instead_of_crashing_the_frame():
    face, _ = make()

    def boom(x):
        raise RuntimeError("interpreter crashed")

    face.runner = boom  # replace the callable outright; FakeRunner.__call__ can't be patched per-instance
    assert face.estimate(FRAME, HEAD) is None
    # a second failing call must not raise either (no crash from repeated model errors)
    assert face.estimate(FRAME, HEAD) is None


def test_latency_stats_count_calls():
    face, _ = make()
    assert face.latency_stats()["n"] == 0
    face.estimate(FRAME, HEAD)
    face.estimate(FRAME, HEAD)
    assert face.latency_stats()["n"] == 2


def test_far_away_jaw_point_does_not_blow_up_the_head_box():
    face, _ = make()
    far = {**HEAD, "lower_jaw": (110, 190, 0.95)}  # confident but 90 px below the head
    assert face.head_box(far) == pytest.approx(face.head_box(HEAD))


def test_nearby_jaw_and_mouth_points_extend_the_box():
    face, _ = make()
    near = {**HEAD, "lower_jaw": (110, 120, 0.9)}  # just below the nose: within the secondary margin
    assert face.head_box(near)[2] > face.head_box(HEAD)[2]
