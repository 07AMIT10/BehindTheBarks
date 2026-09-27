# Clip handoff: Data → Web

For each fallback clip, Person A delivers a directory with three files. Paths are listed in the
manifest (`data/fallback/manifest.json`); until it exists, Web uses its own
`backend/demo/clips/manifest.json`.

## Clip directory

- `clip.mp4` — H.264, yuv420p, +faststart (browsers need this; OpenCV `mp4v` does NOT play in
  `<video>`). Any size; 640 px on the long side is plenty.
- `events.jsonl` — one JSON object per line: `{"t": <seconds from clip start>, "type": "frame" | "audio" | "rules" | "treat", "data": {...}}`.
  For `frame`/`audio`/`rules`, `data` is the contract model with `data.ts == t`. `treat` lines carry
  `data: {"ts": t}` at each treat-drop moment. Frames at ~8 Hz (every processed frame, including
  `dog_detected: false`); audio debounced as usual; rules once per frame.
- `meta.json` — `{"name": "Treat drop", "emotion": "excited", "duration_s": 42.0}` (emotion = the
  clip's dominant label, for the clip picker).

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
