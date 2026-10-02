"""scripts/export_android_models.py: Task 0.1's deliverable is the export *report*, so these tests
pin its schema, the per-format routing, and both ways an export can go wrong (a converter that
raises, a source file that is missing) -- each must land in the report instead of killing the run,
because "a failure is a result, not a blocker".

Every converter the real script drives (litert_torch, onnx2tf, ultralytics, deeplabcut, HTTP) is
too slow or needs weights to run in a unit test, so `BACKENDS` -- the seam the CLI drives them
through -- is injected with fakes. The fakes write *real* artifacts (a tiny ONNX graph, a tiny
.tflite) with one distinct input shape per candidate, so every assertion below reads the report the
script really produces rather than the fakes' call log.
"""

import hashlib
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import export_android_models as E  # noqa: E402

REPORT_KEYS = {"format", "path", "input_shape", "dtype", "sha256", "export_ok", "notes"}

# The candidate set and formats are the brief's spec, and one distinct input shape per candidate is
# what each fake writes: an entry's recorded input_shape therefore says which backend produced the
# file, so swapping two backends fails here.
SPEC = {
    "det_yolo26n_320_fp32": "tflite",
    "det_yolo26n_320_int8": "tflite",
    "det_effdet_lite0_320_int8": "tflite",
    "det_picodet_s_320": "onnx",
    "det_picodet_s_320_tflite": "tflite",
    "pose_superanimal_hrnet_w32": "tflite",
    "pose_superanimal_hrnet_w32_onnx": "onnx",
    "pose_rtmpose_s_ap10k": "onnx",
    "pose_rtmpose_m_ap10k": "onnx",
    "pose_rtmpose_m_ap10k_tflite": "tflite",
    "pose_rtmpose_ap10k_litert": "tflite",
    "face_dog_landmarks_384": "tflite",
    "audio_yamnet": "tflite",
}
FAKE_INPUT_SHAPES = {
    "det_yolo26n_320_fp32": [1, 32, 1],
    "det_yolo26n_320_int8": [1, 33, 1],
    "det_effdet_lite0_320_int8": [1, 34, 1],
    "det_picodet_s_320": [1, 3, 224, 224],
    "det_picodet_s_320_tflite": [1, 35, 1],
    "pose_superanimal_hrnet_w32": [1, 36, 1],
    "pose_superanimal_hrnet_w32_onnx": [1, 3, 256, 256],
    "pose_rtmpose_s_ap10k": [1, 3, 192, 192],
    "pose_rtmpose_m_ap10k": [1, 3, 128, 128],
    "pose_rtmpose_m_ap10k_tflite": [1, 37, 1],
    "pose_rtmpose_ap10k_litert": [1, 40, 1],
    "face_dog_landmarks_384": [1, 38, 1],
    "audio_yamnet": [1, 39, 1],
}
BOOM = "litert_torch refused the graph: aten::slow_conv2d is not lowerable"


def _tiny_onnx(path: Path, shape: list[int]) -> Path:
    """A real, loadable ONNX graph whose input shape is `shape` (identity per candidate)."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper

    shape = [int(d) for d in shape]
    x = helper.make_tensor_value_info("images", TensorProto.FLOAT, shape)
    graph = helper.make_graph([helper.make_node("Identity", ["images"], ["out"])], "g", [x], [x])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 9
    onnx.save(model, str(path))
    return path


def _tiny_tflite(path: Path, shape: list[int]) -> Path:
    """A real .tflite whose input shape is `shape` (identity per candidate)."""
    pytest.importorskip("tensorflow")
    import tensorflow as tf

    width, channels = int(shape[-2]), int(shape[-1])
    inputs = tf.keras.Input(shape=(width, channels), batch_size=1, dtype=tf.float32)
    model = tf.keras.Model(inputs, tf.keras.layers.Dense(3)(inputs))
    path.write_bytes(tf.lite.TFLiteConverter.from_keras_model(model).convert())
    return path


def _fake_backends(fail: str | None = None) -> dict[str, E.Backend]:
    """A fake per real backend name, writing a real artifact of the candidate's own shape."""
    assert set(FAKE_INPUT_SHAPES) == {c.name for c in E.CANDIDATES}, "candidate set changed"

    def make(name: str) -> E.Backend:
        def run(cand: E.Candidate, job: E.Job) -> Path:
            if cand.name == fail:
                raise RuntimeError(BOOM)
            shape = FAKE_INPUT_SHAPES[cand.name]
            dest = job.out_dir / f"{cand.name}.{cand.fmt}"
            return _tiny_onnx(dest, shape) if cand.fmt == "onnx" else _tiny_tflite(dest, shape)

        run.__name__ = f"fake_{name}"
        return run

    return {name: make(name) for name in E.BACKENDS}


def _run(tmp_path: Path, *extra: str, fail: str | None = None) -> dict:
    """Run the CLI with fake backends and return the report it wrote."""
    out = tmp_path / "out"
    assert E.main(["--out", str(out), *extra], backends=_fake_backends(fail)) == 0
    return json.loads((out / E.REPORT_NAME).read_text())


# -- the plan the candidates and the report follow ------------------------------------------------


def test_dry_run_lists_every_candidate_with_its_backend_and_touches_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(E, "BACKENDS", {name: _explode(name) for name in E.BACKENDS})
    assert E.main(["--dry-run", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    for cand in E.CANDIDATES:
        assert f"{cand.name} {cand.fmt} {cand.backend}" in out
    assert list(tmp_path.iterdir()) == [], "--dry-run wrote into the output directory"


def test_dry_run_works_from_a_clean_interpreter(tmp_path):
    """The script must import nothing heavy just to print its plan."""
    done = subprocess.run(
        [sys.executable, "scripts/export_android_models.py", "--dry-run", "--out", str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    for cand in E.CANDIDATES:
        assert cand.name in done.stdout
    assert list(tmp_path.iterdir()) == []


def test_report_has_exactly_the_agreed_schema_for_every_candidate(tmp_path):
    report = _run(tmp_path)
    assert set(report) == set(SPEC)
    for name, entry in report.items():
        assert set(entry) == REPORT_KEYS, name
        assert entry["format"] == SPEC[name]
        assert entry["export_ok"] is True
        assert entry["dtype"] == "float32"
        assert Path(entry["path"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest()


def test_each_artifact_is_written_by_the_backend_for_its_own_format(tmp_path):
    report = _run(tmp_path)
    for name, entry in report.items():
        shape = FAKE_INPUT_SHAPES[name]
        assert Path(entry["path"]).suffix == f".{SPEC[name]}"
        assert entry["input_shape"] == shape, name
        assert entry["notes"], name


def test_a_converter_that_raises_is_recorded_with_its_own_message_and_the_rest_still_export(tmp_path):
    report = _run(tmp_path, fail="pose_superanimal_hrnet_w32")
    bad = report["pose_superanimal_hrnet_w32"]
    assert set(bad) == REPORT_KEYS
    assert bad["export_ok"] is False
    assert BOOM in bad["notes"]
    assert (bad["format"], bad["path"], bad["input_shape"], bad["dtype"], bad["sha256"]) == (
        "tflite",
        None,
        None,
        None,
        None,
    )
    assert report["pose_superanimal_hrnet_w32_onnx"]["export_ok"] is True  # the ONNX fallback ran
    assert report["det_yolo26n_320_fp32"]["export_ok"] is True  # one failure did not abort the run


def test_only_runs_the_candidates_it_was_given(tmp_path):
    report = _run(tmp_path, "--only", "det_picodet_s_320", "--only", "audio_yamnet")
    assert set(report) == {"det_picodet_s_320", "audio_yamnet"}


def test_notes_carry_the_weight_licence_of_each_candidate(tmp_path):
    report = _run(tmp_path)
    for name, needle in [
        ("det_yolo26n_320_fp32", "AGPL"),
        ("det_effdet_lite0_320_int8", "Apache-2.0"),
        ("det_picodet_s_320", "Apache-2.0"),
        ("pose_rtmpose_m_ap10k", "Apache-2.0"),
        ("face_dog_landmarks_384", "CC BY-NC"),
    ]:
        assert needle in report[name]["notes"], name


def test_notes_record_the_source_of_every_candidate(tmp_path):
    report = _run(tmp_path)
    for cand in E.CANDIDATES:
        assert cand.source in report[cand.name]["notes"], cand.name


# -- the two backends that are cheap enough to test for real --------------------------------------


def test_a_copied_candidate_is_byte_identical_to_its_source(tmp_path):
    src = _tiny_tflite(tmp_path / "dog_face_landmarks_full.tflite", [1, 384, 3])
    cand = E.Candidate(
        name="face_x", fmt="tflite", backend="copy", source="unit test", license="CC BY-NC 4.0", local=src
    )
    dest = E.copy_local(cand, E.Job(out_dir=tmp_path / "out"))
    assert dest == tmp_path / "out" / "face_x.tflite"
    assert dest.read_bytes() == src.read_bytes()


def test_a_missing_source_file_is_reported_with_the_command_that_fetches_it(tmp_path):
    missing = tmp_path / "definitely_absent.tflite"
    cand = E.Candidate(
        name="face_x", fmt="tflite", backend="copy", source="unit test", license="x", local=missing
    )
    with pytest.raises(FileNotFoundError) as excinfo:
        E.copy_local(cand, E.Job(out_dir=tmp_path / "out"))
    assert str(missing) in str(excinfo.value) and "fetch_face_model.py" in str(excinfo.value)


def test_download_keeps_the_first_source_whose_io_matches_the_candidate(tmp_path, monkeypatch):
    """YAMNet: the MediaPipe and TF-Hub copies are tried in order; only a 15600-in/521-out one ships."""
    cand = next(c for c in E.CANDIDATES if c.name == "audio_yamnet")
    assert list(cand.urls) == [E.YAMNET_MEDIAPIPE_URL, E.YAMNET_TFHUB_URL]
    served = {E.YAMNET_MEDIAPIPE_URL: b"mediapipe-labels-wrong-width", E.YAMNET_TFHUB_URL: b"tfhub-15600x521"}

    def fake_fetch(url: str, dest: Path) -> Path:
        dest.write_bytes(served[url])
        return dest

    def fake_probe(path: Path) -> E.Probe:
        return E.Probe([1, 15600], "float32", [1, 521] if path.read_bytes().startswith(b"tfhub") else [1, 5])

    monkeypatch.setattr(E, "fetch_file", fake_fetch)
    monkeypatch.setattr(E, "probe", fake_probe)
    dest = E.download(cand, E.Job(out_dir=tmp_path))
    assert dest.read_bytes() == b"tfhub-15600x521"


def test_download_fails_loudly_when_no_source_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "fetch_file", lambda url, dest: (dest.write_bytes(b"wrong"), dest)[1])
    monkeypatch.setattr(E, "probe", lambda path: E.Probe([1, 22050], "float32", [1, 5]))
    cand = next(c for c in E.CANDIDATES if c.name == "audio_yamnet")
    with pytest.raises(RuntimeError) as excinfo:
        E.download(cand, E.Job(out_dir=tmp_path))
    assert "15600" in str(excinfo.value) and "521" in str(excinfo.value)


def test_a_download_that_dies_mid_stream_resumes_instead_of_restarting(tmp_path):
    """The pose SDK zip is tens of MB on links that do break mid-stream; a retry must not refetch it."""
    body = b"rtmpose-simcc" * 10_000
    half = len(body) // 2
    asked_for = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            start = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
            asked_for.append(start)
            payload = body[start:half] if start == 0 else body[start:]
            self.send_response(206 if start else 200)
            # The first response promises the whole file and hangs up halfway; the resume is well-formed.
            self.send_header("Content-Length", str(len(body)) if start == 0 else str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        dest = E.fetch_file(f"http://127.0.0.1:{server.server_port}/end2end.onnx", tmp_path / "end2end.onnx")
    finally:
        server.shutdown()
    assert dest.read_bytes() == body
    assert asked_for == [0, half], "the retry did not resume from the bytes already on disk"
    assert list(tmp_path.glob("*.part")) == []


def test_the_ultralytics_export_leaves_only_the_artifact_and_asks_for_litert_at_320(tmp_path, monkeypatch):
    """The output directory is the artifact set Phase 0.3 pushes to the phone, weights included or not."""
    asked = []

    class FakeYOLO:
        def __init__(self, path):
            self.path = Path(path)
            self.path.write_bytes(b"weights")

        def export(self, **kwargs):
            asked.append(kwargs)
            out = self.path.with_suffix(".tflite")
            out.write_bytes(b"tflite-bytes")
            return str(out)

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))
    cand = next(c for c in E.CANDIDATES if c.name == "det_yolo26n_320_fp32")
    dest = E.ultralytics_litert(cand, E.Job(out_dir=tmp_path / "out"))
    assert dest.read_bytes() == b"tflite-bytes"
    assert not (tmp_path / "out" / "_ultralytics").exists(), "the downloaded .pt was left in the output directory"
    assert asked == [{"format": "litert", "imgsz": 320, "quantize": None}]


def test_the_float32_conversion_is_the_artifact_not_the_alphabetically_first_fp16_one(tmp_path):
    """onnx2tf writes both a float32 and a float16 model, and "float16" sorts first."""
    for name in ("det_picodet_s_320_float16.tflite", "det_picodet_s_320_float32.tflite"):
        (tmp_path / name).write_bytes(b"")
    assert E.pick_converted_tflite(tmp_path).name == "det_picodet_s_320_float32.tflite"
    (tmp_path / "det_picodet_s_320_float16.tflite").unlink()
    assert E.pick_converted_tflite(tmp_path).name == "det_picodet_s_320_float32.tflite"
    (tmp_path / "det_picodet_s_320_float32.tflite").unlink()
    with pytest.raises(RuntimeError, match="no .tflite"):
        E.pick_converted_tflite(tmp_path)


def test_a_conversion_that_changes_the_models_outputs_is_a_failure(tmp_path):
    """onnx2tf rewrites mmdeploy's SimCC outputs from (1,17,512) to (1,1,512): the keypoints vanish."""
    src = _tiny_onnx(tmp_path / "src.onnx", [1, 21, 3])  # output shape == input shape
    faithful = _tiny_tflite(tmp_path / "faithful.tflite", [21, 3])  # (1, 21, 3) in and out
    E.assert_same_outputs(src, faithful)  # same outputs: a faithful conversion

    lossy = _tiny_tflite(tmp_path / "lossy.tflite", [7, 3])  # (1, 7, 3) instead
    with pytest.raises(RuntimeError) as excinfo:
        E.assert_same_outputs(src, lossy)
    assert "[1, 21, 3]" in str(excinfo.value) and "[1, 7, 3]" in str(excinfo.value)


def test_a_conversion_that_will_not_execute_is_a_failure(tmp_path):
    """The rtmpose conversion exits 0 and then fails to run, which a shape check alone would miss."""
    src = _tiny_onnx(tmp_path / "src.onnx", [1, 21, 3])
    junk = tmp_path / "junk.tflite"
    junk.write_bytes(_tiny_tflite(tmp_path / "good.tflite", [21, 3]).read_bytes())
    junk.write_bytes(junk.read_bytes()[:64])  # a truncated flatbuffer: loads as garbage, runs as nothing
    with pytest.raises(Exception):  # noqa: B017  (any interpreter error is a failure worth recording)
        E.assert_same_outputs(src, junk)


def test_an_onnx_export_is_left_as_one_self_contained_file(tmp_path):
    """torch.onnx.export can spill the weights into a sidecar; a phone ships one file per model."""
    onnx = pytest.importorskip("onnx")
    from onnx import TensorProto, helper, numpy_helper

    weights = numpy_helper.from_array(np.arange(4, dtype=np.float32), name="w")
    node = helper.make_node("Mul", ["x", "w"], ["y"])
    shape = [1, 4]
    val = helper.make_tensor_value_info("x", TensorProto.FLOAT, shape)
    model = helper.make_graph([node], "g", [val], [val], initializer=[weights])
    proto = helper.make_model(model, opset_imports=[helper.make_opsetid("", 17)])
    proto.ir_version = 9
    onnx.save_model(proto, str(tmp_path / "m.onnx"), save_as_external_data=True, all_tensors_to_one_file=True,
                    location="m.onnx.data", size_threshold=0)
    assert (tmp_path / "m.onnx.data").exists(), "the fixture did not spill external data"

    E.inline_external_data(tmp_path / "m.onnx")
    assert not (tmp_path / "m.onnx.data").exists()
    reloaded = onnx.load(str(tmp_path / "m.onnx"))
    assert numpy_helper.to_array(reloaded.graph.initializer[0]).tolist() == [0.0, 1.0, 2.0, 3.0]


def test_probe_reads_input_shape_and_dtype_from_a_real_artifact_of_each_format(tmp_path):
    tflite = _tiny_tflite(tmp_path / "m.tflite", [1, 21, 1])
    got = E.probe(tflite)
    assert (got.input_shape, got.input_dtype, got.output_shape) == ([1, 21, 1], "float32", [1, 21, 3])
    onnx = _tiny_onnx(tmp_path / "m.onnx", [1, 3, 256, 256])
    got = E.probe(onnx)
    assert (got.input_shape, got.input_dtype) == ([1, 3, 256, 256], "float32")
    assert E.probe(tmp_path / "junk.onnx").input_shape is None  # unreadable -> reported as unknown


def test_probe_never_raises_on_bytes_that_are_not_a_model(tmp_path):
    junk = tmp_path / "junk.tflite"
    junk.write_bytes(b"not a model at all")
    assert E.probe(junk).input_shape is None
    empty = tmp_path / "empty.onnx"
    empty.write_bytes(b"")
    assert E.probe(empty).input_dtype is None


def _explode(name: str) -> E.Backend:
    def run(cand: E.Candidate, job: E.Job) -> Path:
        raise AssertionError(f"--dry-run called the {name} backend")

    return run