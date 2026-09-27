"""Add a TODO row to data/fallback/SOURCES.md for every file in data/fallback/raw/ that isn't listed yet.

    python scripts/sources_stub.py          # append the missing rows
    python scripts/sources_stub.py --check  # list unlisted or TODO rows; exit 1 if any (for a pre-demo check)

Pexels and Pixabay downloads carry their video id in the filename ("pexels-...-4588047 (1080p).mp4",
"pexels_videos_2022395.mp4", "12345-dog-eating_1080p.mp4"), so the page URL and licence name are
filled in as a guess; every guessed URL is marked "(inferred, verify)". Everything else stays TODO.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FALLBACK = ROOT / "data" / "fallback"
VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
PEXELS_LICENCE = "Pexels License (free to use, no attribution required)"
PIXABAY_LICENCE = "Pixabay Content License (free to use, no attribution required)"


def infer(name: str) -> tuple[str, str]:
    """(source URL, licence) guessed from a downloaded file's name; ('TODO', 'TODO') if unrecognised."""
    stem = Path(name).stem.lower()
    if "pexels" in stem:
        ids = re.findall(r"(\d{5,})", stem)
        if ids:
            return f"https://www.pexels.com/video/{ids[-1]}/ (inferred, verify)", PEXELS_LICENCE
    m = re.match(r"^(\d{4,})[-_]", stem)
    if m and ("pixabay" in stem or "-" in stem[m.end():] or "_" in stem[m.end():]):
        return f"https://pixabay.com/videos/id-{m.group(1)}/ (inferred, verify)", PIXABAY_LICENCE
    return "TODO", "TODO"


def guess_shows(name: str) -> str:
    words = re.sub(r"(pexels|pixabay|videos?|\d+p?|hd|uhd|\(.*?\))", " ", Path(name).stem.lower())
    words = " ".join(re.split(r"[-_\s]+", words)).strip()
    return f"TODO (filename hints: {words})" if words else "TODO"


def listed_files(text: str) -> set[str]:
    """Names in the first column of every table row (with or without the raw/ prefix)."""
    names = set()
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and cells and cells[0] and not set(cells[0]) <= {"-", ":"}:
            names.add(Path(cells[0]).name)
    return names


def todo_rows(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.lstrip().startswith("|") and "TODO" in line]


def missing_rows(raw_dir: Path, text: str) -> list[str]:
    have = listed_files(text)
    rows = []
    for path in sorted(raw_dir.glob("*")):
        if path.suffix.lower() in VIDEO_SUFFIXES and path.name not in have:
            url, licence = infer(path.name)
            rows.append(f"| raw/{path.name} | {url} | {licence} | {guess_shows(path.name)} |")
    return rows


def insert_rows(text: str, rows: list[str]) -> str:
    """Append rows to the end of the first table in the file (the raw clips table)."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.lstrip().startswith("|")), None)
    if start is None:
        raise ValueError("SOURCES.md has no table to add rows to")
    end = start
    while end < len(lines) and lines[end].lstrip().startswith("|"):
        end += 1
    return "\n".join(lines[:end] + rows + lines[end:]) + "\n"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources", type=Path, default=FALLBACK / "SOURCES.md")
    ap.add_argument("--raw-dir", type=Path, default=FALLBACK / "raw")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    text = args.sources.read_text()
    rows = missing_rows(args.raw_dir, text)
    if args.check:
        pending = [r for r in rows] + todo_rows(text)
        for row in pending:
            print(row)
        sys.exit(1 if pending else 0)
    if rows:
        args.sources.write_text(insert_rows(text, rows))
    print(f"added {len(rows)} row(s) to {args.sources}" + ("" if not rows else "; fill in the TODOs"))


if __name__ == "__main__":
    main()
