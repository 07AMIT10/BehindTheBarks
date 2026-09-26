from types import SimpleNamespace

import numpy as np
import pytest

from backend.vision.detect import DogDetector, point_in_zone

ZONE = [[0.2, 0.4], [0.8, 0.4], [0.8, 0.95], [0.2, 0.95]]
CFG = {"data": {"feeding_zone": ZONE, "detect": {"ema_alpha": 0.5, "ema_reset_misses": 2, "crop_pad": 0.1}}}
FRAME = np.zeros((100, 200, 3), np.uint8)  # h=100, w=200


class FakeModel:
    """Stands in for ultralytics YOLO: returns whatever boxes the test queues up."""

    names = {0: "person", 16: "dog", 17: "cat"}

    def __init__(self):
        self.queue = []

    def __call__(self, frame, **kw):
        rows = self.queue.pop(0) if self.queue else []
        xyxy = np.array([r[:4] for r in rows], dtype=np.float32).reshape(-1, 4)
        conf = np.array([r[4] for r in rows], dtype=np.float32)
        cls = np.array([r[5] for r in rows], dtype=np.float32)
        return [SimpleNamespace(boxes=SimpleNamespace(xyxy=xyxy, conf=conf, cls=cls))]


def make(**detect):
    cfg = {"data": {**CFG["data"], "detect": {**CFG["data"]["detect"], **detect}}}
    model = FakeModel()
    return DogDetector(cfg, model=model, device="cpu"), model


def test_zone_membership():
    assert point_in_zone((100, 60), ZONE, FRAME.shape)
    assert not point_in_zone((100, 10), ZONE, FRAME.shape)  # above the zone
    assert not point_in_zone((10, 60), ZONE, FRAME.shape)  # left of the zone


def test_zone_uses_bottom_center_by_default():
    det, model = make()
    # centre (100, 50) is in the zone, but the feet at (100, 98) are below it.
    model.queue.append([(80, 2, 120, 98, 0.9, 16)])
    assert det.detect(FRAME).in_feeding_zone is False
    det, model = make(zone_anchor="center")
    model.queue.append([(80, 2, 120, 98, 0.9, 16)])
    assert det.detect(FRAME).in_feeding_zone is True


def test_only_dogs_are_kept():
    det, model = make()
    model.queue.append([(0, 0, 50, 50, 0.99, 0), (0, 0, 60, 60, 0.95, 17)])  # person and cat
    assert det.detect(FRAME) is None


def test_best_dog_by_confidence_then_area():
    det, model = make()
    model.queue.append([(0, 0, 10, 10, 0.60, 16), (100, 40, 140, 90, 0.90, 16)])
    assert det.detect(FRAME).conf == pytest.approx(0.90)
    det, model = make()
    model.queue.append([(0, 0, 10, 10, 0.901, 16), (100, 40, 180, 90, 0.899, 16)])  # tie at 2 dp: bigger wins
    assert det.detect(FRAME).raw_bbox == (100, 40, 180, 90)


def test_ema_smoothing_and_reset():
    det, model = make()
    model.queue += [[(100, 40, 140, 90, 0.9, 16)], [(110, 40, 150, 90, 0.9, 16)]]
    det.detect(FRAME)
    d = det.detect(FRAME)
    assert d.bbox[0] == pytest.approx(105) and d.raw_bbox[0] == 110
    # Two misses (ema_reset_misses=2) restart smoothing, so the next box is taken as-is.
    model.queue += [[], [], [(120, 40, 160, 90, 0.9, 16)]]
    assert det.detect(FRAME) is None and det.detect(FRAME) is None
    assert det.detect(FRAME).bbox == (120, 40, 160, 90)


def test_big_jump_does_not_drag_the_box():
    det, model = make()
    model.queue += [[(0, 0, 30, 30, 0.9, 16)], [(150, 60, 190, 95, 0.9, 16)]]
    det.detect(FRAME)
    assert det.detect(FRAME).bbox == (150, 60, 190, 95)


def test_crop_is_padded_clipped_and_maps_back():
    det, _ = make()
    crop, (ox, oy) = det.crop(FRAME, (100, 40, 140, 90))  # pad 0.1 -> 4 px in x, 5 px in y
    assert (ox, oy) == (96, 35) and crop.shape[:2] == (60, 48)
    crop, (ox, oy) = det.crop(FRAME, (0, 0, 50, 50), pad=0.5)  # clipped at the frame edge
    assert (ox, oy) == (0, 0) and crop.shape[:2] == (75, 75)
    crop, _ = det.crop(FRAME, (150, 50, 200, 100), pad=0.5)
    assert crop.shape[0] <= 100 and crop.shape[1] <= 200


def test_latency_is_recorded():
    det, model = make()
    model.queue.append([])
    det.detect(FRAME)
    assert det.latency_stats()["n"] == 1


def test_model_without_dog_class_rejected():
    model = FakeModel()
    model.names = {0: "person"}
    with pytest.raises(ValueError):
        DogDetector(CFG, model=model, device="cpu")
