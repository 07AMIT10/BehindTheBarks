import numpy as np
import pytest

from backend.contracts import FrameEvent
from backend.vision import face as F
from backend.vision.detect import Detection
from backend.vision.features import FeatureExtractor
from backend.vision.keypoint_map import CANONICAL_NAMES

CFG = {"data": {"keypoint_conf_threshold": 0.3, "features": {}}}
FPS = 8
BODY = 200.0  # body length in pixels


def det(bbox=(100, 100, 420, 300), in_zone=True):
    return Detection(bbox=bbox, conf=0.9, in_feeding_zone=in_zone, raw_bbox=bbox)


def kps_with(conf=0.9, **points):
    kps = {n: None for n in CANONICAL_NAMES}
    kps.update({k: (v[0], v[1], conf) for k, v in points.items()})
    return kps


def body(scale=1.0, flip=False, tail_tip=(-90.0, 0.0), ox=0.0, oy=0.0, **extra):
    """Dog in profile, facing right (or left if flip): withers at (200, 200), hip BODY px behind it.

    tail_tip is relative to the tail base, in body-frame pixels: x backwards, y up.
    """
    sx = -1 if flip else 1
    pts = {"withers": (200, 200), "hip": (200 - sx * BODY, 200), "tail_base": (200 - sx * BODY, 200)}
    pts["tail_tip"] = (pts["tail_base"][0] - sx * tail_tip[0], pts["tail_base"][1] - tail_tip[1])
    pts.update(extra)
    return kps_with(**{k: ((v[0] * scale) + ox, (v[1] * scale) + oy) for k, v in pts.items()})


def run(fx, frames, fps=FPS, t0=0.0, d=None):
    """Feed (kps, lms) pairs at fps; return the list of FrameEvents."""
    return [fx.update(t0 + i / fps, d or det(), k, l) for i, (k, l) in enumerate(frames)]


def make():
    return FeatureExtractor(CFG)


# -- tail_height -----------------------------------------------------------------------------


@pytest.mark.parametrize("flip", [False, True])
def test_tucked_tail_is_below_minus_half(flip):
    ev = run(make(), [(body(flip=flip, tail_tip=(-20, -80)), None)] * 6)[-1]  # tip down and forward, between the legs
    assert ev.features.tail_height < -0.5


@pytest.mark.parametrize("flip", [False, True])
def test_raised_tail_is_above_half_and_level_tail_near_zero(flip):
    up = run(make(), [(body(flip=flip, tail_tip=(-30, 90)), None)] * 6)[-1]
    level = run(make(), [(body(flip=flip, tail_tip=(-90, 0)), None)] * 6)[-1]
    assert up.features.tail_height > 0.5
    assert abs(level.features.tail_height) < 0.1


def test_tail_height_ignores_camera_distance_and_position():
    a = run(make(), [(body(tail_tip=(-30, 60)), None)] * 6)[-1].features.tail_height
    b = run(make(), [(body(scale=0.5, ox=300, oy=40, tail_tip=(-30, 60)), None)] * 6)[-1].features.tail_height
    assert a == pytest.approx(b, abs=0.02)


def test_tail_height_follows_a_tilted_back_line():
    # the dog is tilted 20 degrees (head down a slope); a tail level with the back is still level
    th = np.radians(20)
    rot = lambda x, y: (200 + (x - 200) * np.cos(th) - (y - 200) * np.sin(th), 200 + (x - 200) * np.sin(th) + (y - 200) * np.cos(th))
    k = kps_with(withers=rot(200, 200), hip=rot(0, 200), tail_base=rot(0, 200), tail_tip=rot(-90, 200))
    assert abs(run(make(), [(k, None)] * 6)[-1].features.tail_height) < 0.1


def test_tail_height_none_when_tail_missing_or_low_confidence():
    assert run(make(), [(body(tail_tip=(-30, 90)) | {"tail_tip": None}, None)] * 4)[-1].features.tail_height is None
    weak = body(tail_tip=(-30, 90))
    weak["tail_tip"] = (*weak["tail_tip"][:2], 0.2)  # below the 0.3 threshold, even if the caller did not filter it
    assert run(make(), [(weak, None)] * 4)[-1].features.tail_height is None


def test_tail_height_none_when_back_line_is_vertical():
    k = kps_with(withers=(200, 100), hip=(200, 300), tail_base=(200, 300), tail_tip=(250, 320))  # dog seen end-on
    assert run(make(), [(k, None)] * 4)[-1].features.tail_height is None


# -- tail_wag_hz -----------------------------------------------------------------------------


def wag_frames(hz, n, fps=FPS, amp=30.0, drop=(), jitter=0.0, seed=0):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        y = amp * np.sin(2 * np.pi * hz * i / fps) + rng.normal(0, jitter)
        out.append((body(tail_tip=(-60, y)), None))
    return [f for i, f in enumerate(out) if i not in drop]


@pytest.mark.parametrize("hz", [1.0, 2.0, 3.0])
def test_sinusoidal_tail_measures_its_frequency(hz):
    ev = run(make(), wag_frames(hz, 40))[-1]
    assert ev.features.tail_wag_hz == pytest.approx(hz, abs=0.3)


def test_wag_measured_along_any_axis_and_with_pose_jitter():
    # a wag seen from behind is horizontal in the image: the tail tip swings in x, not y
    frames = []
    for i in range(40):
        k = body(tail_tip=(-60, 0))
        tb = k["tail_base"]
        k["tail_tip"] = (tb[0] + 30 * np.sin(2 * np.pi * 2 * i / FPS), tb[1] - 60, 0.9)
        frames.append((k, None))
    assert run(make(), frames)[-1].features.tail_wag_hz == pytest.approx(2.0, abs=0.3)
    assert run(make(), wag_frames(2.0, 40, jitter=2.0))[-1].features.tail_wag_hz == pytest.approx(2.0, abs=0.3)


def test_wag_survives_irregular_timing_and_a_few_dropped_frames():
    fx = make()
    frames = wag_frames(2.0, 44)
    rng = np.random.default_rng(1)
    t, evs = 0.0, []
    for i, (k, l) in enumerate(frames):
        # sample the same 2 Hz sinusoid at irregular times (frames dropped by the source)
        t += 1 / FPS * rng.choice([1, 1, 1, 2])
        y = 30 * np.sin(2 * np.pi * 2.0 * t)
        evs.append(fx.update(t, det(), body(tail_tip=(-60, y)), None))
    hz = [e.features.tail_wag_hz for e in evs if e.features.tail_wag_hz is not None]
    assert hz and hz[-1] == pytest.approx(2.0, abs=0.35)


def test_still_tail_is_zero_hz_not_none():
    assert run(make(), [(body(tail_tip=(-60, 5)), None)] * 40)[-1].features.tail_wag_hz == 0.0


def test_wag_none_with_too_few_or_broken_samples():
    assert run(make(), wag_frames(2.0, 6))[-1].features.tail_wag_hz is None  # under a second of data
    fx = make()
    frames = wag_frames(2.0, 40)
    # tail visible in the first frames, then hidden for 2 s, then a single frame: no unbroken run
    frames = [f if (i < 6 or i == 39) else (body() | {"tail_tip": None}, None) for i, f in enumerate(frames)]
    assert run(fx, frames)[-1].features.tail_wag_hz is None


def test_wag_none_when_tail_never_seen():
    assert run(make(), [(body() | {"tail_tip": None}, None)] * 30)[-1].features.tail_wag_hz is None


# -- ear_position ----------------------------------------------------------------------------


def head(l_tip, r_tip, nose=(300, 130)):
    """Head facing right, eyes 30 px behind the nose; ear bases 10 px above the eyes."""
    return body(
        nose=nose,
        left_eye=(nose[0] - 30, nose[1] - 10),
        right_eye=(nose[0] - 30, nose[1] - 12),
        left_ear_base=(nose[0] - 45, nose[1] - 22),
        right_ear_base=(nose[0] - 45, nose[1] - 24),
        left_ear_tip=(nose[0] - 45 + l_tip[0], nose[1] - 22 + l_tip[1]),
        right_ear_tip=(nose[0] - 45 + r_tip[0], nose[1] - 24 + r_tip[1]),
    )


def ear(kps):
    return run(make(), [(kps, None)] * 8)[-1].features.ear_position


def test_ears_up_back_neutral():
    assert ear(head((3, -40), (3, -40))) == "up"  # pointing at the sky
    assert ear(head((-40, 3), (-40, 3))) == "back"  # lying along the head, away from the nose
    assert ear(head((4, 40), (4, 40))) == "neutral"  # hanging
    assert ear(head((-30, -30), (-30, 30))) == "neutral"  # ears disagree


def test_missing_ears_are_unknown():
    assert ear(head((3, -40), (3, -40)) | {"left_ear_tip": None, "right_ear_tip": None}) == "unknown"
    assert ear(body()) == "unknown"  # no head keypoints at all


def test_one_visible_ear_is_enough():
    assert ear(head((3, -40), (3, -40)) | {"right_ear_tip": None}) == "up"


def test_back_is_not_claimed_when_the_head_points_straight_down():
    # head pointing down, ears pointing up: 2D-identical to pinned-back ears; must not read as "back"
    k = body(
        nose=(300, 220), left_eye=(300, 190), right_eye=(304, 190),
        left_ear_base=(300, 175), right_ear_base=(304, 175), left_ear_tip=(300, 135), right_ear_tip=(304, 135),
    )
    assert ear(k) == "up"


def test_ear_vote_smooths_a_single_bad_frame():
    fx = make()
    frames = [(head((3, -40), (3, -40)), None)] * 6 + [(head((-40, 3), (-40, 3)), None)] + [(head((3, -40), (3, -40)), None)] * 2
    assert run(fx, frames)[-1].features.ear_position == "up"


def face_lms(gap=0.0, ear_tip=(0, -40), size=100.0):
    """A synthetic 46-point face facing right, eyes/nose spanning `size` px; `gap` = lip gap / size."""
    L = np.zeros((F.N_LANDMARKS, 2))
    L[list(F.EYE_LEFT)] = (0.3 * size, 0.0)
    L[list(F.EYE_RIGHT)] = (0.3 * size, 0.0)
    L[16], L[17] = (0.25 * size, 0.0), (0.25 * size, 0.0)
    L[list(F.NOSE)] = (size, 0.0)  # level with the eyes, so the head axis is horizontal
    L[24], L[35] = (size, -0.5 * size), (size, 0.5 * size)  # give the eye+nose span a size x size box
    L[list(F.EAR_LEFT)] = (0.2 * size, -0.2 * size)
    L[list(F.EAR_RIGHT)] = (0.2 * size, -0.2 * size)
    L[F.EAR_TIP_LEFT] = L[F.EAR_TIP_RIGHT] = (0.2 * size + ear_tip[0], -0.2 * size + ear_tip[1])
    L[F.LIP_UPPER_MID] = (0.9 * size, 0.4 * size)
    L[F.LIP_LOWER_MID] = (0.9 * size, 0.4 * size + gap * float(np.hypot(size, size)))  # ratio is over the diagonal
    return L.tolist()


def test_face_landmarks_are_used_for_ears_when_present():
    ev = run(make(), [(body(), face_lms(ear_tip=(3, -40)))] * 8)[-1]
    assert ev.features.ear_position == "up"
    ev = run(make(), [(body(), face_lms(ear_tip=(-60, 3)))] * 8)[-1]
    assert ev.features.ear_position == "back"


# -- mouth_open ------------------------------------------------------------------------------


def mouth(gap):
    return run(make(), [(body(), face_lms(gap=gap))] * 10)[-1].features.mouth_open


def test_mouth_open_closed_open_and_monotonic():
    assert mouth(0.0) == 0.0
    assert mouth(0.02) == 0.0  # below the closed floor
    assert mouth(0.4) == 1.0
    assert mouth(0.05) < mouth(0.12) < mouth(0.2)


def test_mouth_open_none_without_face_landmarks_and_ignores_face_size():
    assert run(make(), [(body(), None)] * 6)[-1].features.mouth_open is None
    a = run(make(), [(body(), face_lms(gap=0.14, size=100))] * 10)[-1].features.mouth_open
    b = run(make(), [(body(), face_lms(gap=0.14, size=40))] * 10)[-1].features.mouth_open
    assert a == pytest.approx(b, abs=0.02)


# -- body_lowering ---------------------------------------------------------------------------


def stand_then(drop_px, stand_s=6.0, low_s=3.0, bbox_bottom=400.0):
    """Dog stands for stand_s seconds, then withers/hip sit drop_px lower for low_s seconds."""
    fx = make()
    out = []
    for i in range(int((stand_s + low_s) * FPS)):
        t = i / FPS
        dy = 0.0 if t < stand_s else drop_px
        k = body(oy=dy)
        out.append(fx.update(t, det(bbox=(100, 100, 420, bbox_bottom)), k, None))
    return out


def test_body_lowering_reads_high_after_a_crouch_and_zero_while_standing():
    evs = stand_then(drop_px=80)  # height 200 body-px above the bbox bottom -> 120: a 40% drop
    assert evs[int(5 * FPS)].features.body_lowering == pytest.approx(0.0, abs=0.05)
    assert evs[-1].features.body_lowering > 0.7
    small = stand_then(drop_px=4)
    assert small[-1].features.body_lowering == pytest.approx(0.0, abs=0.05)  # inside the dead band


def test_body_lowering_is_none_until_there_is_a_baseline():
    evs = stand_then(drop_px=0, stand_s=6.0, low_s=0.0)
    assert evs[0].features.body_lowering is None
    assert evs[int(1.0 * FPS)].features.body_lowering is None
    assert evs[-1].features.body_lowering is not None


def test_body_lowering_none_without_withers_and_hip():
    fx = make()
    evs = [fx.update(i / FPS, det(), kps_with(nose=(300, 150)), None) for i in range(40)]
    assert evs[-1].features.body_lowering is None


# -- motion_energy ---------------------------------------------------------------------------


def moving(speed_bl_per_s, scale=1.0, n=30):
    fx = make()
    out = []
    for i in range(n):
        dx = speed_bl_per_s * BODY * scale * (i / FPS)  # pixels moved so far
        k = body(scale=scale, ox=dx, nose=(300, 130), left_front_paw=(250, 300), left_back_paw=(80, 300))
        out.append(fx.update(i / FPS, det(), k, None))
    return out[-1].features.motion_energy


def test_motion_energy_static_zero_and_walking_high():
    assert moving(0.0) == 0.0
    assert moving(0.1) == 0.0  # inside the pose-jitter floor
    assert moving(0.6) == pytest.approx((0.6 - 0.2) / (1.0 - 0.2), abs=0.08)
    assert moving(1.5) == 1.0


def test_motion_energy_normalised_by_body_size():
    assert moving(0.5, scale=1.0) == pytest.approx(moving(0.5, scale=0.4), abs=0.03)


def test_wagging_tail_alone_is_not_motion():
    assert run(make(), wag_frames(3.0, 40))[-1].features.motion_energy == 0.0


def test_motion_energy_single_jittery_keypoint_does_not_count():
    fx = make()
    rng = np.random.default_rng(0)
    for i in range(30):
        k = body(nose=(300, 130), left_front_paw=(250, 300))
        k["left_front_paw"] = (250 + rng.normal(0, 60), 300 + rng.normal(0, 60), 0.9)  # one wild point among four
        ev = fx.update(i / FPS, det(), k, None)
    assert ev.features.motion_energy == 0.0


def test_motion_none_with_no_body_points():
    fx = make()
    evs = [fx.update(i / FPS, det(), kps_with(tail_tip=(0, 0)), None) for i in range(10)]
    assert evs[-1].features.motion_energy is None


# -- FrameEvent building ---------------------------------------------------------------------


def test_frame_event_carries_detection_keypoints_landmarks_and_zone():
    lms = face_lms()
    ev = make().update(1.5, det(bbox=(1, 2, 3, 4), in_zone=False), body(), lms)
    assert isinstance(ev, FrameEvent)
    assert ev.ts == 1.5 and ev.source == "live" and ev.dog_detected
    assert ev.bbox == (1, 2, 3, 4) and ev.bbox_conf == 0.9
    assert ev.features.in_feeding_zone is False
    assert len(ev.face_landmarks) == F.N_LANDMARKS
    assert set(ev.body_keypoints) == set(CANONICAL_NAMES)
    assert FrameEvent.model_validate_json(ev.to_json()) == ev


def test_low_confidence_keypoints_are_dropped_from_the_event():
    k = body()
    k["nose"] = (10.0, 10.0, 0.2)
    ev = make().update(0.0, det(), k, None)
    assert ev.body_keypoints["nose"] is None and ev.body_keypoints["withers"] is not None


def test_no_dog_event_has_null_features_and_zone_false():
    ev = make().update(0.0, None, {}, None)
    assert ev.dog_detected is False and ev.bbox is None and ev.body_keypoints == {} and ev.face_landmarks is None
    f = ev.features.model_dump()
    assert f.pop("in_feeding_zone") is False
    assert all(v is None for v in f.values())


def test_dog_with_no_keypoints_still_gives_a_valid_event():
    ev = make().update(0.0, det(), {}, None)
    assert ev.dog_detected and ev.features.in_feeding_zone is True
    f = ev.features.model_dump()
    f.pop("in_feeding_zone")
    assert {k: v for k, v in f.items() if v is not None} == {"ear_position": "unknown"}


def test_source_label_is_configurable():
    assert FeatureExtractor(CFG, source="fallback:clip1").update(0.0, None, {}, None).source == "fallback:clip1"


def test_window_forgets_after_the_dog_leaves():
    fx = make()
    for i in range(30):
        fx.update(i / FPS, det(), body(tail_tip=(-60, 30 * np.sin(2 * np.pi * 2 * i / FPS))), None)
    for i in range(30, 50):  # dog gone for 2.5 s
        fx.update(i / FPS, None, {}, None)
    ev = fx.update(50 / FPS, det(), body(), None)
    assert ev.features.tail_wag_hz is None and ev.features.motion_energy is None


def test_reset_clears_history():
    fx = make()
    run(fx, wag_frames(2.0, 40))
    fx.reset()
    assert fx.update(0.0, det(), body(), None).features.tail_wag_hz is None


def test_config_overrides_defaults():
    fx = FeatureExtractor({"data": {"features": {"tail_height_scale": 0.2, "smooth": {"tail_height": 1.0}}}})
    ev = run(fx, [(body(tail_tip=(-30, 40)), None)] * 4)[-1]  # 40 px up = 0.2 body lengths = +1 at scale 0.2
    assert ev.features.tail_height == pytest.approx(1.0, abs=0.01)
