#!/usr/bin/env python3
"""Export merged config.yaml + config.mobile.yaml data section to JSON for Android assets.

Usage:
    python scripts/export_data_config.py [--out android/app/src/main/assets/config_data.json]
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import shutil
import sys
import yaml

ROOT = Path(__file__).resolve().parent.parent


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


def load_merged_data_config(config_path: Path = ROOT / "config.yaml", mobile_path: Path = ROOT / "config.mobile.yaml") -> dict:
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing base config: {config_path}")
    base = yaml.safe_load(config_path.read_text())
    if mobile_path.is_file():
        mobile = yaml.safe_load(mobile_path.read_text())
        if mobile:
            base = deep_merge(base, mobile)
    return base.get("data", {})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "android" / "app" / "src" / "main" / "assets" / "config_data.json",
        help="Path to output JSON file",
    )
    parser.add_argument(
        "--copy-spec",
        action="store_true",
        default=True,
        help="Also copy mobile_spec.json to assets",
    )
    args = parser.parse_args()

    data_cfg = load_merged_data_config()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data_cfg, indent=2) + "\n")
    print(f"Exported data config to {args.out}")

    if args.copy_spec:
        spec_src = ROOT / "backend" / "vision" / "mobile_spec.json"
        if spec_src.is_file():
            spec_dst = args.out.parent / "mobile_spec.json"
            shutil.copyfile(spec_src, spec_dst)
            print(f"Copied mobile_spec.json to {spec_dst}")


if __name__ == "__main__":
    main()
