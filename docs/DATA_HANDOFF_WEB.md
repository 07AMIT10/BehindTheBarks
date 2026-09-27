# Clip handoff: Data → Web

For each fallback clip, Person A delivers a directory with three files. Paths are listed in the
manifest (`data/fallback/manifest.json`); until it exists, Web uses its own
`backend/demo/clips/manifest.json`.

## Clip directory (Person A's live format — preferred)

`data/fallback/manifest.json`: `{"version": 1, "ts_origin": "clip_start", "clips": [...]}` where each
entry has `name`, `expected_emotion`, `treats` (seconds), `notes`, `clip` (path to the mp4),
`duration_s` and `events` (path to the jsonl). Event lines are `{"type", "data"}` with the
clip-relative time in `data.ts` (from `run_pipeline.py --rebase-ts`); treat moments come from the
manifest `treats` list. The web side also accepts its placeholder schema (entries with `id`/`dir`,
lines with an explicit `t`) for `backend/demo/clips/`.

- `clip.mp4` — H.264, yuv420p, +faststart (browsers need this; OpenCV `mp4v` does NOT play in
  `<video>`). Any size; 640 px on the long side is plenty.
- `events.jsonl` — one JSON object per line: `{"type": "frame" | "audio" | "rules", "data": {...}}`
  with `data.ts` in clip-relative seconds. Frames at ~8 Hz (every processed frame, including
  `dog_detected: false`); audio debounced as usual; rules once per frame.
- `meta.json` (placeholder clips only) — `{"name", "emotion", "duration_s"}`.

Produce it from a real run: `python scripts/fake_phone.py clip.mp4 --events out/<id>.jsonl`
(timestamped from 0), or dump any Pipeline run's callbacks in the same shape.
Then check it: `.venv/bin/python scripts/validate_clip.py data/fallback/manifest.json` (exit 0 = valid).

## Mismatch report template (Step 10)

When the real pipeline misbehaves, file it like this before fixing anything:

- Symptom: <what the dashboard shows>
- Raw evidence: <the offending events.jsonl lines or WS messages>
- Contracts check: <`validate_clip.py` / contracts.py result>
- Owner: <Data | Web> — <one line why>
- Web-side workaround (if any): <none | description>
