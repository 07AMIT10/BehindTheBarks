"""scripts/bakeoff_models.py: the deliverable is the CSV, so these tests pin the brief's schema and check that the
numbers in it mean what the header says.

Everything real is stubbed: a 10-frame synthetic clip and runners that return boxes and keypoints with a known
relationship to the reference's. That keeps the test to the measurement logic (recall, IoU, per-keypoint PCK, the
unmapped-keypoint accounting, the timing column) instead of to seven clips and 200 MB of weights -- the actual
accuracy numbers come from `python scripts/bakeoff_models.py --clips all`, and this file is the guard rail that the
table it writes cannot quietly change shape or quietly start dropping the keypoints a candidate cannot supply.
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import bakeoff_models as B  # noqa: E402

CANONICAL = set(B.CANONICAL_NAMES)
# The two keypoint schemas the real candidates use, so the mapping accounting is exercised on a real shape: 39
# SuperAnimal names covering everything, and AP-10K's 17 covering nine canonical names and no tail at all.
STUB_BODYPARTS = tuple(f"sa_{n}" for n in B.CANONICAL_NAMES)
STUB_AP10K = tuple(B.AP10K_TO_CANONICAL)


def write_clip(path: Path, n_frames: int = 10, size: tuple[int, int] = (160, 120)) -> Path:
    """A synthetic clip: a moving bright block on a dark background, `n_frames` of them."""
    import cv2

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 8, size)
    assert writer.isOpened(), "cv2 cannot write an mp4 here"
    for i in range(n_frames):
        frame = np.full((size[1], size[0], 3), 20, np.uint8)
        cv2.rectangle(frame, (20 + 3 * i, 30), (70 + 3 * i, 90), (30, 60, 200), -1)
        writer.write(frame)
    writer.release()
    return path


def manifest_for(path: Path, clip: Path) -> Path:
    path.write_text(json.dumps({"version": 1, "ts_origin": "clip_start", "clips": [{"name": clip.stem}]}))
    return path


def export_report(names, out_dir: Path) -> Path:
    """A report where every candidate exported, with paths that need not exist: the runners are stubbed."""
    out_dir.mkdir(parents=True, exist_ok=True)
    art = out_dir / "stub.tflite"
    art.write_bytes(b"not a real model")
    path = out_dir / "export_report.json"
    path.write_text(json.dumps({
        name: {
            "format": "tflite", "path": str(art), "input_shape": [1, 3, 8, 8], "dtype": "float32",
            "sha256": "0" * 64, "export_ok": True, "notes": f"{name} notes",
        }
        for name in names
    }))
    return path


class StubRuntime:
    """Stands in for the timed runtime, so `ms_mean_cpu` has something in it."""

    def __init__(self, ms: float = 12.5):
        self.ms_values = [ms, ms, ms]
        self.mean_ms = ms
        self.warm = False

    def reset_timing(self):
        pass

    def __call__(self, x):
        return [np.zeros((1, 1), np.float32)]


class StubServer:
    """The reference: a dog in every frame at a known box, keypoints on a known grid, face ok from frame 4 on."""

    def __init__(self, box=(20.0, 20.0, 140.0, 110.0), miss_from: int | None = None):
        self.box = box
        self.miss_from = miss_from
        self.calls = 0
        self.ms: list[float] = []
        self.mapping = {n: n for n in B.CANONICAL_NAMES}
        self.audio_det = object()  # any truthy stand-in: bakeoff_clip only checks the reference has one

    def label_map(self):
        return {"bark": [15]}, 0.3

    def detect(self, frame):
        self.calls += 1
        return None if self.miss_from is not None and self.calls >= self.miss_from else (self.box, 0.9)

    def pose(self, frame, bbox):
        x1, y1, x2, y2 = bbox
        step = max((x2 - x1) / len(CANONICAL), 1.0)
        return {n: (x1 + i * step, y1 + 10.0, 0.9) for i, n in enumerate(B.CANONICAL_NAMES)}

    def face(self, frame, kps):
        return self.calls >= 4

    def audio(self, window):
        return "bark", 0.8


class StubCandidate:
    """A candidate whose answer relative to the reference is known, so the expected numbers are hand-checkable."""

    def __init__(self, name, modality, *, box=None, miss=None, shift=0.0, mapping=None, names=(),
                 face_ok=None, audio_label="bark", rt_ms=12.5, raises=False):
        self.name = name
        self.modality = modality
        self._box, self._miss, self._shift = box, miss, shift
        self.mapping = dict(mapping or {})
        self.names = tuple(names)
        self._face_ok = face_ok
        self._audio_label = audio_label
        self.rt = StubRuntime(rt_ms)
        self._raises = raises

    def detect(self, frame):
        if self._raises:
            raise RuntimeError("the converter produced a graph that will not run")
        n = getattr(self, "_i", 0)
        self._i = n + 1
        if self._miss is not None and n >= self._miss:
            return None
        x1, y1, x2, y2 = self._box or (20.0, 20.0, 140.0, 110.0)
        return (x1 + self._shift, y1, x2 + self._shift, y2), 0.9

    def pose(self, frame, bbox):
        if self._raises:
            raise RuntimeError("the converter produced a graph that will not run")
        x1, y1, x2, y2 = bbox
        step = max((x2 - x1) / len(CANONICAL), 1.0)
        ref = {n: (x1 + i * step, y1 + 10.0, 0.9) for i, n in enumerate(B.CANONICAL_NAMES)}
        return {n: (p[0] + self._shift, p[1], p[2]) if n in self.mapping.values() else None
                for n, p in ref.items()}

    def face(self, frame, kps):
        if self._raises:
            raise RuntimeError("boom")
        return bool(self._face_ok)

    def audio(self, window):
        return self._audio_label, 0.8


def run_bakeoff(tmp_path, stubs, *, export_names, clip_n=10, server=None, report_mutator=None):
    """Run the CLI against a synthetic clip with stub runners; returns (rows, csv_path)."""
    clip = write_clip(tmp_path / "clips" / "synthetic.mp4", n_frames=clip_n)
    manifest = manifest_for(tmp_path / "manifest.json", clip)
    export_dir = tmp_path / "android_models"
    report_path = export_report(export_names, export_dir)
    if report_mutator is not None:
        report = json.loads(report_path.read_text())
        report_path.write_text(json.dumps(report_mutator(report)))
    out = tmp_path / "bakeoff.csv"
    code = B.main(
        ["--clips", "synthetic", "--out", str(out), "--export-dir", str(export_dir),
         "--clips-dir", str(tmp_path / "clips"), "--manifest", str(manifest), "--no-audio"],
        builders={name: (lambda s=stub: s) for name, stub in stubs.items()},
        reference=server if server is not None else StubServer(),
    )
    assert code == 0
    with out.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    return rows, out


ALL_NAMES = [c.name for c in __import__("export_android_models").CANDIDATES]


# -- the brief's Step 1: the CSV a bake-off writes ------------------------------------------------------


def test_ten_frame_synthetic_clip_writes_the_brief_columns(tmp_path):
    stub = StubCandidate("pose_superanimal_hrnet_w32", "pose", mapping={n: n for n in B.CANONICAL_NAMES},
                         names=STUB_BODYPARTS)
    rows, out = run_bakeoff(tmp_path, {"pose_superanimal_hrnet_w32": stub}, export_names=ALL_NAMES)

    header = out.read_text().splitlines()[0].split(",")
    for column in B.REQUIRED_COLUMNS:
        assert column in header, f"the brief's column {column!r} is missing"
    assert header[:5] == ["model", "clip", "frames", "det_recall_vs_ref", "mean_iou_vs_ref"]
    # one PCK column per canonical keypoint, and all 19 of them: that is what the brief asks for
    pck_cols = [c[len("kp_pck@0.1_vs_ref."):] for c in header if c.startswith("kp_pck@0.1_vs_ref.")]
    assert sorted(pck_cols) == sorted(B.CANONICAL_NAMES)
    row = next(r for r in rows if r["model"] == "pose_superanimal_hrnet_w32")
    assert row["clip"] == "synthetic" and row["frames"] == "10"
    assert row["kp_pck@0.1_vs_ref.nose"] == "1.0"
    assert row["ms_mean_cpu"] == "12.5"


def test_every_candidate_gets_a_row_and_the_failing_ones_keep_their_reason(tmp_path):
    """A candidate that failed to export, and one that fails at run time, are both rows -- not omissions."""
    stubs = {
        "pose_superanimal_hrnet_w32": StubCandidate(
            "pose_superanimal_hrnet_w32", "pose", mapping={n: n for n in B.CANONICAL_NAMES}),
        "det_yolo26n_320_fp32": StubCandidate("det_yolo26n_320_fp32", "det", raises=True),
        **{n: StubCandidate(n, B.MODALITY[n]) for n in ALL_NAMES
           if n not in ("pose_superanimal_hrnet_w32", "det_yolo26n_320_fp32")},
    }
    assert set(stubs) == set(ALL_NAMES), "stub every candidate so no real loader is reached"
    def fail_two(report):
        for name in ("pose_rtmpose_s_ap10k", "pose_rtmpose_m_ap10k_tflite"):
            report[name] = {"format": "tflite", "path": None, "input_shape": None, "dtype": None,
                            "sha256": None, "export_ok": False,
                            "notes": "RuntimeError: the conversion dropped the keypoint axis"}
        return report

    rows, _ = run_bakeoff(tmp_path, stubs, export_names=ALL_NAMES, report_mutator=fail_two)
    seen = {r["model"] for r in rows}
    assert seen == {"server_reference", *ALL_NAMES}, f"missing rows for {seen ^ {'server_reference', *ALL_NAMES}}"
    assert all(r["clip"] == "synthetic" and r["frames"] == "10" for r in rows)

    failed = next(r for r in rows if r["model"] == "det_yolo26n_320_fp32")
    assert failed["status"].startswith("failed: RuntimeError"), failed["status"]
    assert failed["det_recall_vs_ref"] == "", "a candidate that raised has no numbers"
    for name in ("pose_rtmpose_s_ap10k", "pose_rtmpose_m_ap10k_tflite"):
        row = next(r for r in rows if r["model"] == name)
        assert "not exported" in row["status"], row["status"]
        assert "dropped the keypoint axis" in row["notes"] and row["export_ok"] == "False"
        assert row["unmapped_canonical_kps"] == "", "an unexported candidate has no schema to report"


# -- what the numbers mean ------------------------------------------------------------------------------


def test_recall_and_iou_are_separated_by_a_missed_frame(tmp_path):
    """The candidate finds 9 of the reference's 10 boxes; the missing one costs recall *and* IoU."""
    stub = StubCandidate("det_yolo26n_320_fp32", "det", miss=9)
    rows, _ = run_bakeoff(tmp_path, {"det_yolo26n_320_fp32": stub}, export_names=["det_yolo26n_320_fp32"])
    row = next(r for r in rows if r["model"] == "det_yolo26n_320_fp32")
    assert float(row["det_recall_vs_ref"]) == 0.9
    # the nine hits are the reference box exactly, so the mean IoU is 9/10
    assert float(row["mean_iou_vs_ref"]) == 0.9
    assert row["kp_pck@0.1_vs_ref.nose"] == "", "a detector has no keypoints to report"


def test_iou_is_computed_against_the_reference_box_not_the_smoothed_one(tmp_path):
    """A candidate shifted by 20 px on a 120x90 box scores well below 1, which is the whole point of the column."""
    stub = StubCandidate("det_yolo26n_320_fp32", "det", shift=20.0)
    rows, _ = run_bakeoff(tmp_path, {"det_yolo26n_320_fp32": stub}, export_names=["det_yolo26n_320_fp32"])
    row = next(r for r in rows if r["model"] == "det_yolo26n_320_fp32")
    assert 0.7 < float(row["mean_iou_vs_ref"]) < 0.95, row["mean_iou_vs_ref"]
    assert float(row["det_recall_vs_ref"]) == 1.0


def test_pck_is_scored_against_the_reference_box_radius(tmp_path):
    """PCK@0.1 means 10% of the reference box's diagonal: inside is a hit, outside is a miss."""
    box = (20.0, 20.0, 140.0, 110.0)
    radius = 0.1 * ((box[2] - box[0]) ** 2 + (box[3] - box[1]) ** 2) ** 0.5
    assert 0 < radius < 20
    for shift, want in ((radius - 1, 1.0), (radius + 1, 0.0)):
        stub = StubCandidate("pose_superanimal_hrnet_w32", "pose", shift=shift,
                             mapping={n: n for n in B.CANONICAL_NAMES})
        rows, _ = run_bakeoff(tmp_path, {"pose_superanimal_hrnet_w32": stub},
                              export_names=["pose_superanimal_hrnet_w32"], server=StubServer(box=box))
        row = next(r for r in rows if r["model"] == "pose_superanimal_hrnet_w32")
        assert float(row["kp_pck@0.1_vs_ref.nose"]) == want, (shift, row["kp_pck@0.1_vs_ref.nose"])


def test_a_candidate_that_omits_a_keypoint_misses_it_instead_of_being_ignored(tmp_path):
    stub = StubCandidate("pose_rtmpose_ap10k_litert", "pose", mapping=B.AP10K_TO_CANONICAL, names=STUB_AP10K)
    rows, _ = run_bakeoff(tmp_path, {"pose_rtmpose_ap10k_litert": stub}, export_names=["pose_rtmpose_ap10k_litert"])
    row = next(r for r in rows if r["model"] == "pose_rtmpose_ap10k_litert")
    assert float(row["kp_pck@0.1_vs_ref.left_eye"]) == 1.0
    assert row["kp_pck@0.1_vs_ref.tail_tip"] == "", "an unmapped keypoint must not be scored as a miss"
    assert int(row["n_mapped"]) == len(B.AP10K_TO_CANONICAL) == 9
    unmapped = row["unmapped_canonical_kps"].split()
    assert "tail_tip" in unmapped and "left_ear_tip" in unmapped, unmapped
    assert len(unmapped) == len(CANONICAL) - len(B.AP10K_TO_CANONICAL)
    # the aggregate averages the mapped keys only
    assert float(row["kp_pck@0.1_vs_ref"]) == 1.0
    assert row["tail_tip_visible_rate"] == "0.0", "no tail point in the schema means no tail at all"


def test_tail_tip_and_face_columns_are_the_rates_they_say(tmp_path):
    pose = StubCandidate("pose_superanimal_hrnet_w32", "pose", mapping={n: n for n in B.CANONICAL_NAMES})
    face = StubCandidate("face_dog_landmarks_384", "face", face_ok=True)
    rows, _ = run_bakeoff(tmp_path, {"pose_superanimal_hrnet_w32": pose, "face_dog_landmarks_384": face},
                          export_names=["pose_superanimal_hrnet_w32", "face_dog_landmarks_384"])
    p = next(r for r in rows if r["model"] == "pose_superanimal_hrnet_w32")
    f = next(r for r in rows if r["model"] == "face_dog_landmarks_384")
    assert float(p["tail_tip_visible_rate"]) == 1.0
    assert p["face_ok_rate"] == "", "a pose candidate does not run the face model, so it reports no rate"
    assert float(f["face_ok_rate"]) == 1.0
    assert f["tail_tip_visible_rate"] == ""
    assert float(f["ms_mean_cpu"]) == 12.5


def test_the_reference_row_is_the_baseline_and_its_keypoints_are_all_mapped(tmp_path):
    stub = StubCandidate("pose_superanimal_hrnet_w32", "pose", mapping={n: n for n in B.CANONICAL_NAMES})
    rows, _ = run_bakeoff(tmp_path, {"pose_superanimal_hrnet_w32": stub}, export_names=["pose_superanimal_hrnet_w32"])
    ref = next(r for r in rows if r["model"] == "server_reference")
    assert ref["status"] == "reference"
    assert ref["det_recall_vs_ref"] == "1.0" and ref["mean_iou_vs_ref"] == "1.0"
    assert int(ref["n_mapped"]) == len(CANONICAL) == 19
    assert ref["unmapped_canonical_kps"] == ""
    assert float(ref["tail_tip_visible_rate"]) == 1.0
    assert float(ref["face_ok_rate"]) == 0.7, "the stub's face is ok from frame 4 of 10"


# -- the parts that decide what the CSV can be trusted to say ------------------------------------------


def test_pick_best_matches_the_reference_selection_rule():
    """`detect.py` takes the highest confidence rounded to 2dp and breaks ties on the larger area."""
    boxes = np.array([[0, 0, 10, 10], [0, 0, 20, 20], [0, 0, 5, 5]], np.float32)
    scores = np.array([0.501, 0.5021, 0.4999], np.float32)  # all three round to 0.50
    assert B.pick_best(boxes, scores) == 1, "equal confidences must go to the larger box"
    assert B.pick_best(boxes, np.array([0.1, 0.2, 0.9], np.float32)) == 2, "the highest confidence wins outright"
    assert B.pick_best(np.zeros((0, 4)), np.zeros((0,))) is None


def test_ap10k_mapping_leaves_the_tail_unmapped_on_purpose():
    """AP-10K has a root-of-tail point and nothing else on the tail: tail_base and tail_tip have no counterpart."""
    assert "Root of tail" in B.AP10K_TO_CANONICAL
    assert B.AP10K_TO_CANONICAL["Root of tail"] == "hip"
    mapped = set(B.AP10K_TO_CANONICAL.values())
    assert "tail_tip" not in mapped and "tail_base" not in mapped
    assert not (mapped & {"left_ear_tip", "right_ear_tip", "left_ear_base", "right_ear_base"})
    assert len(B.AP10K_NAMES) == 17 and len(mapped) == 9


def test_critical_keypoints_have_their_own_columns():
    header_cols = set(B.CSV_COLUMNS)
    for name in B.CRITICAL_KPS:
        assert f"kp_pck@0.1_vs_ref.{name}" in header_cols, name


def test_the_letterbox_prep_and_the_tensor_it_builds_agree():
    """`PosePrep.to_crop` and the image `pose_input` builds must invert each other, or every keypoint shifts.

    Catches the pad/scale mismatch that would silently shift letterboxed SuperAnimal keypoints: the hand-computed
    mapping is pinned exactly, and a block pattern is pushed through the real tensor so a wrong pad or scale lands
    on a different block's colour. Letterbox must not stretch (sx == sy) where a 2:1 crop must (sx != sy).
    """
    h, w = 100, 200
    yy, xx = np.mgrid[0:h,0:w]
    block = (yy // 4) * 50 + (xx // 4)  # 4x4 blocks, each a colour no neighbour shares
    crop = np.stack([(block * 73) % 256, (block * 151) % 256, (block * 199) % 256], -1).astype(np.uint8)

    letterbox = B.pose_prep(crop, "letterbox")  # fits 100x200 into 256: scale 1.28, 64 rows of pad top and bottom
    assert (letterbox.sx, letterbox.sy) == pytest.approx((1.28, 1.28)), "letterbox must preserve the aspect ratio"
    assert (letterbox.left, letterbox.top) == (0.0, 64.0)
    assert letterbox.to_crop(128, 128) == pytest.approx((100, 50)), "input centre -> crop centre"
    assert letterbox.to_crop(0, 64) == pytest.approx((0, 0)), "first content row of the input -> crop top"
    assert letterbox.to_crop(256, 192) == pytest.approx((200, 100)), "input bottom-right content -> crop corner"

    stretch = B.pose_prep(crop, "stretch")
    assert (stretch.sx, stretch.sy) == pytest.approx((1.28, 2.56)), "stretch must distort a 2:1 crop"
    assert (stretch.left, stretch.top) == (0.0, 0.0)
    assert stretch.to_crop(128, 128) == pytest.approx((100, 50))

    def block_colour_at_input(mode: str, px: int, py: int) -> np.ndarray:
        prep = B.pose_prep(crop, mode)
        tensor = B.pose_input(crop, prep)
        assert tensor.shape == (1, 3, B.POSE_SIZE, B.POSE_SIZE)
        rgb = (tensor[0].transpose(1, 2, 0) * B.IMAGENET_STD + B.IMAGENET_MEAN) * 255.0
        cx, cy = prep.to_crop(float(px), float(py))
        return rgb[py, px], crop[int(round(cy)), int(round(cx))][::-1]  # BGR -> RGB, the block that went in

    for mode in ("letterbox", "stretch"):
        got, want = block_colour_at_input(mode, 64, 96)
        assert np.allclose(got, want, atol=2), f"{mode}: the tensor lost the block that went in ({got} vs {want})"

    assert B.pose_input(crop).shape == (1, 3, B.POSE_SIZE, B.POSE_SIZE), "the default stays a plain stretch"


def test_importing_the_script_does_not_import_a_model_runtime(tmp_path):
    """`--help` and the test suite must not pay for torch, deeplabcut or ultralytics."""
    done = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, 'scripts'); import bakeoff_models;"
         "print(sorted(m for m in ('torch', 'deeplabcut', 'ultralytics', 'tensorflow') if m in sys.modules))"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().endswith("[]"), done.stdout


def test_missing_export_report_is_a_clear_error(tmp_path):
    with pytest.raises(SystemExit, match="export_android_models"):
        B.main(["--export-dir", str(tmp_path / "nothing"), "--out", str(tmp_path / "x.csv")])


def test_each_reference_row_times_only_its_own_clip(tmp_path):
    """A reference row's `ms_mean_cpu` covers that clip's frames; two clips with different timings must differ."""
    clips_dir = tmp_path / "clips"
    write_clip(clips_dir / "first.mp4", n_frames=10)
    write_clip(clips_dir / "second.mp4", n_frames=6)
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"version": 1, "ts_origin": "clip_start", "clips": [{"name": "first"}, {"name": "second"}]}))
    export_dir = tmp_path / "android_models"
    export_report(ALL_NAMES, export_dir)

    class TimingServer(StubServer):
        """Records 100 ms more per frame than the last one, so a clip's mean identifies its frames."""

        def detect(self, frame):
            out = super().detect(frame)
            self.ms.append(100.0 * self.calls)
            return out

    out = tmp_path / "bakeoff.csv"
    code = B.main(
        ["--clips", "all", "--out", str(out), "--export-dir", str(export_dir),
         "--clips-dir", str(clips_dir), "--manifest", str(tmp_path / "manifest.json"), "--no-audio"],
        builders={name: StubCandidate(name, B.MODALITY[name]) for name in ALL_NAMES},
        reference=TimingServer(),
    )
    assert code == 0
    with out.open(newline="") as fh:
        ref = {r["clip"]: r for r in csv.DictReader(fh) if r["model"] == "server_reference"}
    assert ref["first"]["frames"] == "10" and ref["second"]["frames"] == "6"
    # first clip: frames 1..10 -> mean 550; second: 11..16 -> mean 1350. One shared timing list would give 850 twice.
    assert float(ref["first"]["ms_mean_cpu"]) == 550.0, ref["first"]["ms_mean_cpu"]
    assert float(ref["second"]["ms_mean_cpu"]) == 1350.0, ref["second"]["ms_mean_cpu"]


def test_audio_columns_compare_labels_on_the_same_windows():
    """The audio candidate is scored per window against the reference's own label, skipping silent windows."""
    clip = B.ClipFrames(name="synthetic", frames=[np.zeros((120, 160, 3), np.uint8)])
    clip.windows = []
    for i in range(4):
        w = np.full(B.YAMNET_WINDOW, 0.5, np.float32)
        w[0] = 0.0 if i == 0 else 0.5  # the first window is silent; the rest are compared
        clip.windows.append(w)
    server = StubServer()
    server.audio_label = lambda w: ("silence", 1.0) if float(w[0]) == 0.0 else ("bark", 0.8)
    stub = StubCandidate("audio_yamnet", "audio", audio_label="bark")
    cand = B.Candidate("audio_yamnet", "audio", "Apache-2.0", lambda: stub)
    agree = B.bakeoff_clip(cand, stub, clip, [], server, status="ok", notes="", artifact_mb=4.1, export_ok=True)
    disagree = B.bakeoff_clip(cand, StubCandidate("audio_yamnet", "audio", audio_label="yip"), clip, [],
                              server, status="ok", notes="", artifact_mb=4.1, export_ok=True)
    assert agree["audio_windows"] == 3, "the silent window is skipped, the other three are compared"
    assert float(agree["audio_label_match_vs_ref"]) == 1.0
    assert float(disagree["audio_label_match_vs_ref"]) == 0.0
    assert agree["ms_mean_cpu"] == 12.5 and agree["tail_tip_visible_rate"] == ""
