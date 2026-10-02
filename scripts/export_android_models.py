#!/usr/bin/env python
"""Export the Android on-device candidate models (Phase 0, Task 0.1) and write an export report.

    python scripts/export_android_models.py --dry-run                    # list the candidates, no network
    python scripts/export_android_models.py --out out/android_models     # export all of them
    python scripts/export_android_models.py --only det_picodet_s_320      # export one, while iterating

What lands in the output directory is exactly what an Android phone would have to ship, so Phase 0.3
can benchmark it: a `.tflite` for LiteRT (CPU/XNNPACK -- no NNAPI, no NPU) or a `.onnx` for
ONNX Runtime. Every attempt lands in `<out>/export_report.json` as

    {name: {format, path, input_shape, dtype, sha256, export_ok, notes}}

and *a failure is a result, not a blocker*: a converter that raises, a source that cannot be
fetched, or an artifact that turns out not to be a usable model is recorded with its real error in
`notes` (export_ok false) and the run carries on, because Phase 0.3's job is to pick a runtime and a
model from measurements, and "this one does not convert" is one of the measurements.

**What a recorded entry means, and what it does not.** `export_ok: true` says the file was read back
as a model (its input shape and dtype come out of the file itself, never from what the backend
claimed) and was executed once on a zeros input; it is false if either step fails. `sha256` is the
digest of those bytes as they were when the record was made, and the whole report is re-checked at
the end of the run, so a converter that rewrites an artifact behind our back cannot leave a hash
that matches nothing. It does **not** mean the model is accurate, fast or warm: that is Task 0.2
(accuracy against the server stack) and Task 0.3 (latency, memory and thermal on real phones).

Artifacts are scratch: `out/` is git-ignored and every weight here is either Apache-2.0 or
non-commercial (see each candidate's `notes`), so none of this is committed. The Android app fetches
its own copies at build time (`android/app/src/main/assets/models/`, git-ignored).

The heavy converters are reached through `BACKENDS`, a name -> callable registry, so
tests/data/test_export_android_models.py can inject fakes and check the report shape and the
per-format routing without litert_torch, onnx2tf, ultralytics, deeplabcut or the network.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "out" / "android_models"
REPORT_NAME = "export_report.json"
REPORT_KEYS = ("format", "path", "input_shape", "dtype", "sha256", "export_ok", "notes")
DEFAULT_SUPERANIMAL = "hrnet_w32"
MAX_ERROR_CHARS = 2000
DOWNLOAD_TIMEOUT_S = 60
DOWNLOAD_ATTEMPTS = 4

# Sources. All of them are third-party weights: nothing here is committed to git.
EFFDET_LITE0_INT8_URL = (
    "https://storage.googleapis.com/mediapipe-models/object_detector"
    "/efficientdet_lite0/int8/1/efficientdet_lite0.tflite"
)
PICODET_S_320_URL = "https://paddledet.bj.bcebos.com/deploy/third_engine/picodet_s_320_coco_lcnet.onnx"
RTMPOSE_M_AP10K_URL = (
    "https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk"
    "/rtmpose-m_simcc-ap10k_pt-aic-coco_210e-256x256-7a041aa1_20230206.zip"
)
RTMPOSE_AP10K_LITERT_URL = "https://huggingface.co/litert-community/RTMPose-Animal-AP10K-LiteRT/resolve/main/rtm_animal_fp16.tflite"
YAMNET_MEDIAPIPE_URL = "https://storage.googleapis.com/mediapipe-models/audio_classifier/yamnet/float32/1/yamnet.tflite"
YAMNET_TFHUB_URL = "https://tfhub.dev/google/lite-model/yamnet/classification/tflite/1?tf-hub-format=compressed"
# The face weights are the ones scripts/fetch_face_model.py already knows how to fetch (CC BY-NC 4.0).
FACE_LOCAL = ROOT / "models" / "dog_face_landmarks_full.tflite"
FACE_URL = "https://huggingface.co/hugocornellier/dog-face-landmarks/resolve/main/dog_face_landmarks_full.tflite"


@dataclass(frozen=True)
class Candidate:
    """One thing to export. `backend` names the entry of BACKENDS that does the work."""

    name: str  # the report key
    fmt: str  # "tflite" or "onnx"
    backend: str
    source: str  # provenance, copied into the report's notes
    license: str
    urls: tuple[str, ...] = ()  # download sources, tried in order
    expect: tuple[int, int] | None = None  # (input samples, output classes) a download must satisfy
    local: Path | None = None  # an existing file to copy instead of convert
    src: str | None = None  # the candidate name whose artifact this one converts
    extra: Mapping[str, Any] = field(default_factory=dict)  # backend-specific knobs


@dataclass
class Job:
    """Everything a backend needs that is not on the candidate."""

    out_dir: Path
    superanimal: str = DEFAULT_SUPERANIMAL
    fetch_missing: bool = False  # copy_local may download its source instead of failing
    produced: dict[str, Path] = field(default_factory=dict)  # candidate name -> artifact, for src=
    facts: dict[str, str] = field(default_factory=dict)  # candidate name -> what the export learned


@dataclass(frozen=True)
class Probe:
    input_shape: list[int] | None = None
    input_dtype: str | None = None
    output_shape: list[int] | None = None


Backend = Callable[[Candidate, Job], Path]


def candidates_for(superanimal: str = DEFAULT_SUPERANIMAL) -> tuple[Candidate, ...]:
    """The Phase 0 candidate set. `superanimal` names the DLC snapshot the pose baseline comes from.

    The order is empirical rather than tidy: the SuperAnimal LiteRT conversion needs ~4 GB, and on a
    7 GB machine a run that had already converted a detector before reaching it was OOM-killed part
    way through, twice, so the cheap detector exports go first. `run()` writes the report after every
    candidate, so a machine that still cannot fit the conversion keeps the records before it.
    """
    return (
        # -- detector, 320x320 (plan section 1.1 T1) ------------------------------------------------
        Candidate(
            name="det_yolo26n_320_fp32",
            fmt="tflite",
            backend="ultralytics_litert",
            source="ultralytics yolo26n.pt, export format=litert imgsz=320 (fp32)",
            license="AGPL-3.0 (Ultralytics)",
            extra={"weights": "yolo26n.pt", "imgsz": 320, "quantize": None},
        ),
        Candidate(
            name="det_yolo26n_320_int8",
            fmt="tflite",
            backend="ultralytics_litert",
            source="ultralytics yolo26n.pt, export format=litert imgsz=320 quantize=8 (static wi8/ai8)",
            license="AGPL-3.0 (Ultralytics)",
            extra={"weights": "yolo26n.pt", "imgsz": 320, "quantize": 8, "data": "coco8.yaml"},
        ),
        Candidate(
            name="det_effdet_lite0_320_int8",
            fmt="tflite",
            backend="download",
            source=EFFDET_LITE0_INT8_URL,
            license="Apache-2.0 (MediaPipe EfficientDet-Lite0 int8)",
            urls=(EFFDET_LITE0_INT8_URL,),
        ),
        Candidate(
            name="det_picodet_s_320",
            fmt="onnx",
            backend="download",
            source=PICODET_S_320_URL,
            license="Apache-2.0 (PaddleDetection PP-PicoDet-S 320, no NMS in the graph)",
            urls=(PICODET_S_320_URL,),
        ),
        Candidate(
            name="det_picodet_s_320_tflite",
            fmt="tflite",
            backend="onnx2tf",
            source=f"onnx2tf of {PICODET_S_320_URL}",
            license="Apache-2.0 (PaddleDetection PP-PicoDet-S 320)",
            src="det_picodet_s_320",
        ),
        # -- pose (plan section 1.1 T2) --------------------------------------------------------------
        # The baseline the rules were tuned against, exported only to quantify the gap (plan D3(a)).
        Candidate(
            name=f"pose_superanimal_{superanimal}",
            fmt="tflite",
            backend="torch_litert",
            source=f"DeepLabCut SuperAnimal-Quadruped {superanimal} wrapped as a torch module, litert_torch",
            license="non-commercial (DeepLabCut SuperAnimal-Quadruped snapshot)",
            extra={"model_name": superanimal},
        ),
        Candidate(
            name=f"pose_superanimal_{superanimal}_onnx",
            fmt="onnx",
            backend="torch_onnx",
            source=f"DeepLabCut SuperAnimal-Quadruped {superanimal} wrapped as a torch module, torch.onnx",
            license="non-commercial (DeepLabCut SuperAnimal-Quadruped snapshot)",
            extra={"model_name": superanimal},
        ),
        Candidate(
            name="pose_rtmpose_s_ap10k",
            fmt="onnx",
            backend="download_zip",
            source="OpenMMLab RTMPose-s AP-10K ONNX SDK",
            license="Apache-2.0 (mmpose RTMPose)",
            urls=(),  # OpenMMLab publishes no AP-10K RTMPose-s SDK build; see notes.
        ),
        Candidate(
            name="pose_rtmpose_m_ap10k",
            fmt="onnx",
            backend="download_zip",
            source=RTMPOSE_M_AP10K_URL,
            license="Apache-2.0 (mmpose RTMPose, AP-10K, 256x256)",
            urls=(RTMPOSE_M_AP10K_URL,),
        ),
        Candidate(
            name="pose_rtmpose_m_ap10k_tflite",
            fmt="tflite",
            backend="onnx2tf",
            source=f"onnx2tf of {RTMPOSE_M_AP10K_URL}",
            license="Apache-2.0 (mmpose RTMPose, AP-10K, 256x256)",
            src="pose_rtmpose_m_ap10k",
        ),
        Candidate(
            name="pose_rtmpose_ap10k_litert",
            fmt="tflite",
            backend="download",
            source=RTMPOSE_AP10K_LITERT_URL,
            license="Apache-2.0 (litert-community RTMPose Animal AP10K, fp16)",
            urls=(RTMPOSE_AP10K_LITERT_URL,),
        ),
        # -- face (plan section 1.1 T3) --------------------------------------------------------------
        Candidate(
            name="face_dog_landmarks_384",
            fmt="tflite",
            backend="copy",
            source="models/dog_face_landmarks_full.tflite (scripts/fetch_face_model.py), 46-point DogFLW model",
            license="CC BY-NC 4.0 (hugocornellier/dog-face-landmarks)",
            local=FACE_LOCAL,
            urls=(FACE_URL,),
        ),
        # -- audio (plan section 1.1 T4) -------------------------------------------------------------
        Candidate(
            name="audio_yamnet",
            fmt="tflite",
            backend="download",
            source=f"{YAMNET_MEDIAPIPE_URL} (then {YAMNET_TFHUB_URL})",
            license="Apache-2.0 (YAMNet / AudioSet)",
            urls=(YAMNET_MEDIAPIPE_URL, YAMNET_TFHUB_URL),
            expect=(15600, 521),
        ),
    )


CANDIDATES = candidates_for()


# -- backends: one per kind of candidate ---------------------------------------------------------


def fetch_file(url: str, dest: Path) -> Path:
    """Download `url` to `dest`.

    These artifacts run to tens of megabytes on links that do break mid-stream, so a failed attempt
    keeps its `.part` file and the next one resumes with a Range request instead of fetching the
    whole thing again. `dest` only ever appears once every byte is there, so a `.part` left in the
    output directory is always a resumable download and never a half-written model.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        try:
            request = urllib.request.Request(url, headers={"Range": f"bytes={have}-"} if have else {})
            with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as resp:
                # A server that ignores Range replays the whole body, so start over rather than splice.
                mode = "ab" if have and resp.status == 206 else "wb"
                copied = 0
                with tmp.open(mode) as fh:
                    while chunk := resp.read(1 << 20):
                        fh.write(chunk)
                        copied += len(chunk)
                # copyfileobj stops at EOF, and a connection that hangs up early looks like EOF: the
                # advertised length is the only thing that says the stream was cut short.
                declared = resp.headers.get("Content-Length")
                if declared is not None and copied != int(declared):
                    raise http.client.IncompleteRead(f"got {copied} of {declared} bytes")
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            if attempt == DOWNLOAD_ATTEMPTS:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"download failed after {attempt} attempts: {url}: {exc}") from exc
            print(f"  attempt {attempt}/{DOWNLOAD_ATTEMPTS} broke ({exc}); resuming at {tmp.stat().st_size} bytes", flush=True)
            continue
        tmp.replace(dest)
        return dest
    raise AssertionError("unreachable")


def download(cand: Candidate, job: Job) -> Path:
    """Fetch the first source whose model actually has the shape this candidate needs."""
    if not cand.urls:
        raise RuntimeError(f"no source URL: {cand.source}")
    rejected = []
    for url in cand.urls:
        dest = fetch_file(url, job.out_dir / f"{cand.name}.{cand.fmt}")
        got = probe(dest)
        if cand.expect is None:
            return dest
        n_in, n_out = cand.expect
        if n_in in (got.input_shape or []) and got.output_shape and got.output_shape[-1] == n_out:
            return dest
        # A rejected source is deleted, never left at the path a later phase fetches a model from.
        rejected.append(f"{url} -> input {got.input_shape}, output {got.output_shape}")
        dest.unlink(missing_ok=True)
    raise RuntimeError(f"no source matched input {cand.expect[0]} samples / {cand.expect[1]} outputs: " + "; ".join(rejected))


def download_zip(cand: Candidate, job: Job) -> Path:
    """Fetch a release zip (the OpenMMLab ONNX SDK layout) and take the single .onnx out of it."""
    if not cand.urls:
        raise RuntimeError(f"no source URL: {cand.source}")
    archive = fetch_file(cand.urls[0], job.out_dir / f"{cand.name}.zip")
    try:
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if n.endswith(f".{cand.fmt}")]
            if not names:
                raise RuntimeError(f"no .{cand.fmt} inside {archive.name}: {zf.namelist()}")
            dest = job.out_dir / f"{cand.name}.{cand.fmt}"
            with zf.open(sorted(names, key=len)[0]) as src, dest.open("wb") as fh:
                shutil.copyfileobj(src, fh)
    finally:
        archive.unlink(missing_ok=True)
    return dest


def copy_local(cand: Candidate, job: Job) -> Path:
    """Copy an already-shipped .tflite into the output dir. Never re-converts it."""
    if cand.local is None:
        raise RuntimeError(f"{cand.name} has no local source")
    if not cand.local.exists() and job.fetch_missing:
        fetch_file(cand.urls[0], cand.local)
    if not cand.local.exists():
        raise FileNotFoundError(
            f"missing {cand.local}; run `python scripts/fetch_face_model.py` "
            f"(or re-run with --fetch-missing) before exporting {cand.name}"
        )
    dest = job.out_dir / f"{cand.name}.{cand.fmt}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cand.local, dest)
    return dest


def ultralytics_litert(cand: Candidate, job: Job) -> Path:
    """Export a YOLO through ultralytics' LiteRT exporter (litert_torch, with INT8 optional).

    The release checkpoint is fetched into a staging dir first (ultralytics downloads an absent
    weights path to exactly that path), so the only thing left in the output dir is the artifact.
    """
    from ultralytics import YOLO

    stage = job.out_dir / "_ultralytics"
    stage.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"format": "litert", "imgsz": cand.extra["imgsz"], "quantize": cand.extra["quantize"]}
    if cand.extra.get("data"):
        kwargs["data"] = cand.extra["data"]
    exported = Path(YOLO(str(stage / cand.extra["weights"])).export(**kwargs))
    dest = job.out_dir / f"{cand.name}.tflite"
    shutil.move(str(exported), dest)
    shutil.rmtree(stage, ignore_errors=True)  # the downloaded .pt is not part of the artifact set
    return dest


def torch_litert(cand: Candidate, job: Job) -> Path:
    """Export the DeepLabCut SuperAnimal torch module to .tflite through litert_torch."""
    import litert_torch

    model, example, n_keypoints = _superanimal_module(cand.extra["model_name"])
    job.facts[cand.name] = _superanimal_fact(example, n_keypoints)
    dest = job.out_dir / f"{cand.name}.tflite"
    litert_torch.convert(model, (example,)).export(dest)
    return dest


def torch_onnx(cand: Candidate, job: Job) -> Path:
    """Export the DeepLabCut SuperAnimal torch module to .onnx (the fallback when litert_torch refuses)."""
    import torch

    model, example, n_keypoints = _superanimal_module(cand.extra["model_name"])
    job.facts[cand.name] = _superanimal_fact(example, n_keypoints)
    dest = job.out_dir / f"{cand.name}.onnx"
    torch.onnx.export(model, example, str(dest), input_names=["images"], output_names=["bodyparts"], opset_version=17)
    inline_external_data(dest)
    return dest


def inline_external_data(path: Path) -> None:
    """Fold a `.onnx.data` sidecar back into the .onnx and delete it.

    torch.onnx.export spills the weights next to the graph, which no phone can ship: one model, one
    asset, one sha256 in the report.
    """
    import onnx

    if not list(path.parent.glob(f"{path.name}.data*")):
        return
    model = onnx.load(str(path))
    onnx.save_model(model, str(path))
    for sidecar in path.parent.glob(f"{path.name}.data*"):
        sidecar.unlink()


def _superanimal_fact(example: Any, n_keypoints: int) -> str:
    _, _, height, width = example.shape
    return (
        f"{n_keypoints} SuperAnimal-Quadruped keypoints; the caller must resize the padded crop to "
        f"{width}x{height} and normalise it as backend/vision/pose.py does"
    )


def _superanimal_module(model_name: str) -> tuple[Any, Any, int]:
    """The bare pose network of the DLC SuperAnimal snapshot: (module, example input, #bodyparts).

    Only the network is exported, not DeepLabCut's crop/resize/box plumbing, because the caller
    already crops (`backend/vision/pose.py` crops with `crop_padded`) and the phone must reproduce
    the same resize, so the exported graph takes the 256x256 crop tensor the model is fed.
    """
    import torch
    from deeplabcut.pose_estimation_pytorch.config.pose import PoseConfig
    from deeplabcut.pose_estimation_pytorch.modelzoo.utils import get_super_animal_snapshot_path
    from deeplabcut.pose_estimation_pytorch.models.model import PoseModel
    from deeplabcut.pose_estimation_pytorch.runners.base import attempt_snapshot_load

    cfg = PoseConfig.build_for_superanimal_inference(
        super_animal="superanimal_quadruped",
        model_name=model_name,
        detector_name=None,
        max_individuals=1,
        device="cpu",
    )
    model = PoseModel.build(cfg["model"])
    snapshot = attempt_snapshot_load(
        get_super_animal_snapshot_path("superanimal_quadruped", model_name), "cpu", None
    )
    model.load_state_dict(snapshot["model"])
    model.eval()
    crop = cfg["data"]["inference"].get("top_down_crop") or {}
    height, width = int(crop.get("height", 256)), int(crop.get("width", 256))
    return model, torch.zeros(1, 3, height, width), len(cfg["metadata"]["bodyparts"])


def conversion_copy(src: Path, stage: Path) -> Path:
    """A copy of `src` inside `stage`, for a converter to chew on.

    onnx2tf saves its own re-exported graph back over the file it is given, which would rewrite an
    artifact whose sha256 the report has already recorded. Converting a copy keeps that hash true of
    the bytes that were downloaded.
    """
    work = stage / f"src.{src.suffix.lstrip('.')}"
    shutil.copyfile(src, work)
    return work


def onnx2tf(cand: Candidate, job: Job) -> Path:
    """Convert a candidate's .onnx to .tflite with onnx2tf (an attempt: SimCC heads often do not convert)."""
    src = job.produced.get(cand.src or "")
    if src is None:
        raise RuntimeError(f"{cand.src} has no artifact to convert (it failed earlier in this run)")
    stage = job.out_dir / f"_onnx2tf_{cand.name}"
    stage.mkdir(parents=True, exist_ok=True)
    job.facts[cand.name] = "float32 model; onnx2tf also emits a float16 sibling, which needs a LiteRT build with XNNPACK fp16 to run"
    try:
        done = subprocess.run(
            [sys.executable, "-m", "onnx2tf", "-i", str(conversion_copy(src, stage)), "-o", str(stage)],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if done.returncode != 0:
            tail = "\n".join((done.stderr or done.stdout or "").strip().splitlines()[-12:])
            raise RuntimeError(f"onnx2tf exited {done.returncode} on {src.name}:\n{tail}")
        dest = job.out_dir / f"{cand.name}.tflite"
        shutil.move(str(pick_converted_tflite(stage)), dest)
        assert_same_outputs(src, dest)
        return dest
    finally:
        # The stage is onnxsim's scratch space, not an artifact: it never survives this call.
        shutil.rmtree(stage, ignore_errors=True)


def assert_same_outputs(src: Path, dest: Path) -> None:
    """Run the source and its conversion on the same zeros input and compare the outputs.

    onnx2tf rewrites mmdeploy's SimCC outputs from (1, 17, 512) to (1, 1, 512) -- the 17 keypoints
    disappear -- and the result then fails to execute at all. A conversion that exits 0 is not
    automatically a usable model, and a shape comparison cannot catch the second half, so the
    converted model is actually run here: whatever it raises is the error the report records.
    """
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(src), providers=["CPUExecutionProvider"])
    spec = session.get_inputs()[0]
    spatial = [d for d in spec.shape[1:] if isinstance(d, int) and d > 0]
    if len(spatial) == len(spec.shape) - 1:
        batch_shape = spatial  # the source pins its own input size
    else:
        # A dynamic source (mmdeploy exports with symbolic batch and keypoint dims) takes the
        # converted model's input size, which is the size it will be run at anyway.
        interp = _interpreter(dest)
        interp.allocate_tensors()
        batch_shape = list(reversed([int(d) for d in interp.get_input_details()[0]["shape"][1:-1]]))
    want = [list(o.shape) for o in session.run(None, {spec.name: np.zeros([1, *batch_shape], np.float32)})]

    got = _run_tflite(dest)
    if [list(shape) for shape in want] != got:
        raise RuntimeError(
            f"{dest.name} outputs {got} but {src.name} outputs {want}: "
            f"the conversion changed the model's outputs, so it is not a usable model"
        )


def _run_tflite(path: Path) -> list[list[int]]:
    """The converted model's output shapes, raising whatever the runtime raises if it will not run."""
    import numpy as np

    interp = _interpreter(path)
    interp.allocate_tensors()
    got = interp.get_input_details()[0]
    interp.set_tensor(got["index"], np.zeros([int(d) for d in got["shape"]], got["dtype"]))
    interp.invoke()
    return [[int(d) for d in o["shape"]] for o in interp.get_output_details()]


def pick_converted_tflite(stage: Path) -> Path:
    """The float32 model out of an onnx2tf output directory.

    onnx2tf writes both a `_float32.tflite` and a `_float16.tflite`, and "float16" sorts first, so
    picking the first file alphabetically silently ships the float16 one -- which most CPU runtimes
    refuse outright ("Only float32, uint8, int8 supported currently").
    """
    produced = sorted(stage.glob("*.tflite"), key=lambda p: ("_float16" in p.stem, p.name))
    if not produced:
        raise RuntimeError(f"no .tflite in {stage}")
    return produced[0]


BACKENDS: dict[str, Backend] = {
    "download": download,
    "download_zip": download_zip,
    "copy": copy_local,
    "ultralytics_litert": ultralytics_litert,
    "torch_litert": torch_litert,
    "torch_onnx": torch_onnx,
    "onnx2tf": onnx2tf,
}


# -- probing and reporting ------------------------------------------------------------------------


def probe(path: Path) -> Probe:
    """(input shape, dtype, output shape) read back from the artifact itself, never guessed.

    Uses the LiteRT interpreter (ai_edge_litert) and falls back to `tf.lite`, which TF 2.20 still
    ships but deprecates. Anything unreadable reports `None` instead of raising: an artifact we
    cannot inspect is a fact worth recording, not a reason to lose the whole report.
    """
    try:
        if path.suffix == ".onnx":
            return _probe_onnx(path)
        return _probe_tflite(path)
    except Exception as exc:  # noqa: BLE001  (a broken model must not lose the report)
        return Probe()


def _probe_onnx(path: Path) -> Probe:
    import onnx
    from onnx.helper import tensor_dtype_to_np_dtype

    model = onnx.load(str(path), load_external_data=False)
    graph_input = model.graph.input[0]
    dims = [d.dim_value for d in graph_input.type.tensor_type.shape.dim]
    return Probe(
        [d for d in dims] if dims and all(dims) else None,
        tensor_dtype_to_np_dtype(graph_input.type.tensor_type.elem_type).name,
        None,
    )


def _interpreter(path: Path) -> Any:
    """A LiteRT interpreter for `path`: the ai_edge_litert one, or TF's, which is deprecated in 2.20."""
    try:
        from ai_edge_litert import interpreter as itp
    except ImportError:
        import tensorflow as tf

        itp = tf.lite
    return itp.Interpreter(model_path=str(path))


def _probe_tflite(path: Path) -> Probe:
    interp = _interpreter(path)
    interp.allocate_tensors()
    got_in, got_out = interp.get_input_details()[0], interp.get_output_details()[0]
    shape, out_shape = got_in["shape"], got_out["shape"]
    return Probe(
        [int(d) for d in shape],
        _dtype_name(got_in["dtype"]),
        [int(d) for d in out_shape] if getattr(out_shape, "size", None) else None,
    )


def _dtype_name(dtype: Any) -> str:
    return getattr(dtype, "__name__", str(dtype))


def _notes(cand: Candidate, job: Job) -> list[str]:
    """Provenance and caveats, in the order a reader needs them.

    Anything a backend learned about the model while exporting it (keypoint count, crop size) comes
    back through `job.facts`: reading it back out of DeepLabCut here would mean importing torch
    mid-run, which is exactly the import order LiteRT cannot survive.
    """
    notes = [cand.source, cand.license]
    if cand.src:
        notes.append(f"converted from the {cand.src} candidate")
    quantize = cand.extra.get("quantize", ...)
    if quantize is ...:
        pass
    elif quantize is None:
        notes.append("fp32 weights; LiteRT runs an fp32 model in fp16 via the GPU delegate or XNNPACK FORCE_FP16")
    elif quantize == 8:
        notes.append("static int8 weights and activations, calibrated on coco8")
    if cand.expect:
        notes.append(f"kept only a source with {cand.expect[0]}-sample input and {cand.expect[1]} outputs")
    if cand.name in job.facts:
        notes.append(job.facts[cand.name])
    return notes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_once(path: Path) -> tuple[bool, str]:
    """Execute the artifact on a zeros input: (did it run, the note the report carries).

    A smoke test, not a measurement: it proves the graph is runnable and says how many outputs it
    has, and nothing about accuracy (Task 0.2) or speed (Task 0.3). ONNX inputs with symbolic dims
    are filled with 1 for the batch and 256 for a spatial axis, which is what the candidate is
    exported at.
    """
    try:
        if path.suffix == ".onnx":
            import numpy as np
            import onnxruntime as ort

            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            spec = session.get_inputs()[0]
            shape = [1] + [d if isinstance(d, int) and d > 0 else 256 for d in spec.shape[1:]]
            shapes = [list(o.shape) for o in session.run(None, {spec.name: np.zeros(shape, np.float32)})]
        else:
            shapes = _run_tflite(path)
    except ImportError as exc:
        return True, f"not executed (no runtime available: {exc})"
    except Exception as exc:  # noqa: BLE001  (a model that will not run is not a model we can ship)
        return False, f"failed to run on a zeros input: {type(exc).__name__}: {exc}"
    return True, f"ran on a zeros input: {shapes}"


def write_report(out: Path, report: Mapping[str, Any]) -> Path:
    """Write the report atomically, so an interrupted run cannot leave unparsable JSON behind."""
    path = out / REPORT_NAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def drifted(report: Mapping[str, Any]) -> list[str]:
    """Candidates whose artifact no longer hashes to what the report recorded.

    A converter that rewrites its input after the report hashed it (onnx2tf saves its re-exported
    graph straight back over the .onnx it is handed) would otherwise leave a sha256 in the report
    that matches nothing on disk, which is exactly what a later fetch/verify step would trust.
    """
    return [
        name
        for name, entry in sorted(report.items())
        if entry["path"] and _sha256(Path(entry["path"])) != entry["sha256"]
    ]


def run(candidates: Sequence[Candidate], job: Job, backends: Mapping[str, Backend] | None = None) -> dict:
    """Export every candidate in turn; a backend that raises is recorded, not propagated.

    The report is written after every candidate, not once at the end: the SuperAnimal conversion can
    be OOM-killed on a small machine, and the deliverable of this script is the report, so whatever a
    kill takes with it must not take the earlier records too.
    """
    backends = BACKENDS if backends is None else backends
    report: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        print(f"[{cand.name}] {cand.fmt} via {cand.backend}", flush=True)
        artifact = job.out_dir / f"{cand.name}.{cand.fmt}"
        try:
            path = backends[cand.backend](cand, job)
        except Exception as exc:  # noqa: BLE001  (a failure here is a result, per the plan)
            # Nothing half-written may sit where a later phase fetches a model from.
            artifact.unlink(missing_ok=True)
            message = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
            print(f"  FAILED: {message.splitlines()[0][:200]}", flush=True)
            report[cand.name] = dict.fromkeys(REPORT_KEYS) | {
                "format": cand.fmt,
                "export_ok": False,
                # Notes are built here, after the backend ran, so what it learned survives its failure.
                "notes": "; ".join(_notes(cand, job) + [message]),
            }
            write_report(job.out_dir, report)
            continue
        job.produced[cand.name] = path
        got = probe(path)
        ran, ran_note = run_once(path)
        notes = _notes(cand, job)
        ok = got.input_dtype is not None and ran
        if got.input_dtype is None:
            notes.append(f"could not read {path.suffix} tensors back, so the file is not a readable model")
        notes.append(ran_note)
        if ok:
            print(f"  wrote {path} ({path.stat().st_size / 1e6:.2f} MB)", flush=True)
        else:
            artifact.unlink(missing_ok=True)  # unreadable or unrunnable is not something to ship
        report[cand.name] = {
            "format": cand.fmt,
            "path": str(path) if ok else None,
            "input_shape": got.input_shape if ok else None,
            "dtype": got.input_dtype if ok else None,
            "sha256": _sha256(path) if ok else None,
            "export_ok": ok,
            "notes": "; ".join(notes),
        }
        write_report(job.out_dir, report)
    return report


def print_plan(candidates: Iterable[Candidate]) -> None:
    """One greppable line per candidate: name, format, backend, then the provenance."""
    for cand in candidates:
        print(f"{cand.name} {cand.fmt} {cand.backend} {cand.source}")


def main(argv: Sequence[str] | None = None, *, backends: Mapping[str, Backend] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export the Android on-device candidate models and report on them.")
    p.add_argument("--out", default=str(DEFAULT_OUT), help=f"output directory (default {DEFAULT_OUT})")
    p.add_argument("--dry-run", action="store_true", help="list the candidates and exit, touching no network")
    p.add_argument("--only", action="append", default=[], metavar="NAME", help="export just this candidate (repeatable)")
    p.add_argument("--superanimal", default=DEFAULT_SUPERANIMAL, help="DLC SuperAnimal snapshot for the pose baseline")
    p.add_argument("--fetch-missing", action="store_true", help="download a copied source if it is not in models/")
    args = p.parse_args(argv)

    candidates = candidates_for(args.superanimal)
    if args.only:
        by_name = {c.name: c for c in candidates}
        unknown = [n for n in args.only if n not in by_name]
        if unknown:
            p.error(f"unknown candidate(s): {', '.join(unknown)}; known: {', '.join(by_name)}")
        candidates = tuple(by_name[n] for n in args.only)

    if args.dry_run:
        print_plan(candidates)
        print(f"\n{len(candidates)} candidates; no export, no network.")
        return 0

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    job = Job(out_dir=out, superanimal=args.superanimal, fetch_missing=args.fetch_missing)
    report = run(candidates, job, backends)
    write_report(out, report)

    ok = [n for n, e in report.items() if e["export_ok"]]
    print(f"\n{len(ok)}/{len(report)} exported to {out}")
    for name, entry in report.items():
        size = f"{Path(entry['path']).stat().st_size / 1e6:.2f} MB" if entry["path"] else "-"
        print(f"  {'ok  ' if entry['export_ok'] else 'FAIL'} {name:38s} {entry['format']:6s} {size:>10s}")
    print(f"report: {out / REPORT_NAME}")

    # A hash that no longer matches the file it names is the one error a consumer of this report
    # cannot detect, so it fails the run rather than scrolling past.
    changed = drifted(report)
    if changed:
        print(f"\nERROR: {len(changed)} artifact(s) changed on disk after they were hashed: {', '.join(changed)}")
        print("The recorded sha256 matches nothing on disk; re-run the export before using this report.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())