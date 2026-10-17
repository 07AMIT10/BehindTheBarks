"""Upload the demo MP4s to Cloudflare R2 so the backend can redirect to them (web.demo.video_source: r2).

Reads BTB_R2_ACCOUNT_ID, BTB_R2_ACCESS_KEY_ID, BTB_R2_SECRET_ACCESS_KEY and BTB_R2_BUCKET from the
environment or .env. Safe to re-run: unchanged clips are skipped.

    python scripts/upload_demo_clips.py --dry-run   # show what would upload
    python scripts/upload_demo_clips.py             # upload
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from backend.demo.clips import DemoClips  # noqa: E402
from backend.demo.sync import sync_demo_clips  # noqa: E402
from backend.store.storage import build_r2  # noqa: E402
from backend.web.settings import load_config  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="report only; upload nothing")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    cfg = load_config(ROOT / "config.yaml")
    r2 = build_r2(cfg)
    if r2 is None:
        print("R2 is not configured: set BTB_R2_ACCOUNT_ID, BTB_R2_ACCESS_KEY_ID, "
              "BTB_R2_SECRET_ACCESS_KEY and BTB_R2_BUCKET.", file=sys.stderr)
        return 2
    demo = cfg["web"]["demo"]
    clips = DemoClips(ROOT / demo["manifest"], ROOT / demo["clips_dir"])
    if not clips.available:
        print("no demo clips found in the manifest", file=sys.stderr)
        return 1
    for res in sync_demo_clips(r2, clips, dry_run=args.dry_run):
        print(f"{res.action:<12} {res.key}  ({res.size / 1_000_000:.1f} MB)")
    if not args.dry_run:
        print("done. Set web.demo.video_source: r2 (or BTB_DEMO_VIDEO_SOURCE=r2) to serve from R2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
