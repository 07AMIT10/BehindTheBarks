"""Download the pretrained DogFLW face models into models/ (once; offline afterwards).

    python scripts/fetch_face_model.py            # download anything missing, print tensor details
    python scripts/fetch_face_model.py --force    # re-download

Source: https://huggingface.co/hugocornellier/dog-face-landmarks (trained on DogFLW, Tech4Animals Lab,
University of Haifa). The weights are CC BY-NC 4.0: non-commercial use only. Do not commit them
(`*.tflite` is in .gitignore). Run this on a machine with network before the offline demo.
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

BASE = "https://huggingface.co/hugocornellier/dog-face-landmarks/resolve/main/"
FILES = ("dog_face_landmarks_full.tflite", "dog_face_localizer.tflite")
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def fetch(name: str, force: bool = False) -> Path:
    dest = MODELS_DIR / name
    if dest.exists() and not force:
        print(f"have {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
        return dest
    MODELS_DIR.mkdir(exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"downloading {BASE + name}")
    urllib.request.urlretrieve(BASE + name, tmp)
    tmp.rename(dest)
    print(f"wrote {dest} ({dest.stat().st_size / 1e6:.1f} MB)")
    return dest


def describe(path: Path) -> None:
    import tensorflow as tf

    it = tf.lite.Interpreter(model_path=str(path))
    it.allocate_tensors()
    print(f"\n{path.name}")
    for kind, details in (("input", it.get_input_details()), ("output", it.get_output_details())):
        for d in details:
            print(f"  {kind}: name={d['name']} shape={d['shape'].tolist()} dtype={d['dtype'].__name__}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    paths = [fetch(n, args.force) for n in FILES]
    print("\nLicence: CC BY-NC 4.0 (non-commercial). Source: hugocornellier/dog-face-landmarks on Hugging Face.")
    for path in paths:
        describe(path)


if __name__ == "__main__":
    main()
