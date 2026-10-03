"""Drift guard for pure-Kotlin ports.

Verifies that the commit hash specified in the header comment of each pure-Kotlin port
matches the latest git commit modifying the corresponding Python source file.
If someone modifies features.py, rules.py, wag.py, keypoint_map.py, or yamnet_events.py,
this test fails until the Kotlin port is updated and its header hash bumped.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

PORTS = [
    ("backend/vision/keypoint_map.py", "android/app/src/main/java/com/btb/ondevice/vision/KeypointMap.kt"),
    ("backend/vision/wag.py", "android/app/src/main/java/com/btb/ondevice/vision/WagEstimator.kt"),
    ("backend/vision/features.py", "android/app/src/main/java/com/btb/ondevice/vision/FeatureExtractor.kt"),
    ("backend/fusion/rules.py", "android/app/src/main/java/com/btb/ondevice/fusion/RulesEngine.kt"),
    ("backend/audio/yamnet_events.py", "android/app/src/main/java/com/btb/ondevice/audio/AudioEventDebouncer.kt"),
]


@pytest.mark.parametrize("py_rel, kt_rel", PORTS)
def test_kotlin_port_matches_python_git_hash(py_rel: str, kt_rel: str) -> None:
    py_path = ROOT / py_rel
    kt_path = ROOT / kt_rel

    assert py_path.is_file(), f"Python source file {py_path} does not exist"
    assert kt_path.is_file(), f"Kotlin target file {kt_path} does not exist"

    git_hash = (
        subprocess.check_output(["git", "log", "-n", "1", "--pretty=format:%H", "--", str(py_path)], text=True)
        .strip()
    )
    assert git_hash, f"Could not determine git log commit for {py_path}"

    with kt_path.open() as f:
        first_lines = [f.readline(), f.readline()]

    header_hashes = [
        line.split("Commit:")[1].strip() for line in first_lines if "Commit:" in line
    ]

    assert (
        header_hashes
    ), f"{kt_path} is missing a '// Commit: <sha>' header comment in its first two lines"
    assert (
        header_hashes[0] == git_hash
    ), f"{kt_path} header commit ({header_hashes[0]}) drifted from latest {py_path} commit ({git_hash})"
