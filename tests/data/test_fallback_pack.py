import json
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import mux_audio, precompute_events, sources_stub  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


# -- mux_audio ---------------------------------------------------------------------------------------


def test_parse_add_resolves_esc50_names(tmp_path):
    (tmp_path / "1-100032-A-0.wav").write_bytes(b"x")
    for spec in ("1-100032-A-0@4.5", "1-100032-A-0.wav@4.5"):
        path, at = mux_audio.parse_add(spec, tmp_path)
        assert path == tmp_path / "1-100032-A-0.wav" and at == 4.5


@pytest.mark.parametrize("spec", ["nofile@1", "1-100032-A-0", "1-100032-A-0@soon", "1-100032-A-0@-2"])
def test_parse_add_rejects_bad_specs(tmp_path, spec):
    (tmp_path / "1-100032-A-0.wav").write_bytes(b"x")
    with pytest.raises(ValueError):
        mux_audio.parse_add(spec, tmp_path)


def test_build_filter_lowers_original_only_under_an_overlay():
    plain, has_audio = mux_audio.build_filter(True, [], 0.3, 1.0, True, 1280, 30)
    assert has_audio and "volume=1.0" in plain and "amix" not in plain
    mixed, _ = mux_audio.build_filter(True, [2.0, 6.5], 0.3, 1.0, True, 1280, 30)
    assert "volume=0.3" in mixed and "adelay=2000|2000" in mixed and "adelay=6500|6500" in mixed
    assert "amix=inputs=3" in mixed and mixed.count("silenceremove") == 2


def test_build_filter_without_original_audio_or_overlays():
    graph, has_audio = mux_audio.build_filter(False, [], 0.3, 1.0, True, 1280, 30)
    assert not has_audio and "[a]" not in graph
    graph, has_audio = mux_audio.build_filter(False, [1.0], 0.3, 1.0, False, 0, 30)
    assert has_audio and "silenceremove" not in graph and "anull" in graph


def _make_video(path: Path, seconds=4, audio=True, size="160x90"):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=12:duration={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-c:a", "aac"]
    subprocess.run(cmd + ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def _make_wav(path: Path, lead_silence_s=0.5, tone_s=0.4):
    sr = 16000
    x = np.concatenate([np.zeros(int(lead_silence_s * sr)),
                        0.5 * np.sin(2 * np.pi * 800 * np.arange(int(tone_s * sr)) / sr)])
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


def _pcm(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(float) / 32768


@needs_ffmpeg
def test_mux_places_the_overlay_at_the_requested_time(tmp_path):
    video, wav, out = tmp_path / "v.mp4", tmp_path / "bark.wav", tmp_path / "clips" / "v.mp4"
    _make_video(video)
    _make_wav(wav)
    res = mux_audio.mux(video, [(wav, 2.0)], out)
    assert res["has_audio"] and abs(res["duration"] - 4.0) < 0.3
    pcm = _pcm(out)
    # the 800 Hz overlay is loud right after 2.0 s (its silent lead was trimmed) and absent before it
    assert np.abs(pcm[int(2.05 * 16000):int(2.35 * 16000)]).max() > 0.3
    assert np.abs(pcm[int(1.0 * 16000):int(1.8 * 16000)]).max() < 0.3  # only the original tone, at 0.3 volume


@needs_ffmpeg
def test_mux_handles_silent_video_and_plain_transcode(tmp_path):
    silent, loud = tmp_path / "silent.mp4", tmp_path / "loud.mp4"
    _make_video(silent, audio=False)
    _make_video(loud, size="200x400")
    wav = tmp_path / "bark.wav"
    _make_wav(wav)
    with_bark = mux_audio.mux(silent, [(wav, 1.0)], tmp_path / "a.mp4")
    assert with_bark["has_audio"]
    plain = mux_audio.mux(silent, [], tmp_path / "b.mp4")
    assert not plain["has_audio"]
    portrait = mux_audio.mux(loud, [], tmp_path / "c.mp4", max_side=200)
    assert (portrait["width"], portrait["height"]) == (100, 200)  # portrait: the long side (height) is capped


@needs_ffmpeg
def test_mux_rejects_overlay_past_the_end_and_self_overwrite(tmp_path):
    video, wav = tmp_path / "v.mp4", tmp_path / "bark.wav"
    _make_video(video)
    _make_wav(wav)
    with pytest.raises(ValueError, match="only"):
        mux_audio.mux(video, [(wav, 9.0)], tmp_path / "o.mp4")
    with pytest.raises(ValueError, match="overwrite"):
        mux_audio.mux(video, [], video)


def test_list_dog_clips(tmp_path):
    (tmp_path / "esc50.csv").write_text("filename,fold,target,category\n1-1-A-0.wav,1,0,dog\n1-2-A-14.wav,1,14,chirping_birds\n")
    assert mux_audio.list_dog_clips(tmp_path) == ["1-1-A-0.wav"]


# -- precompute_events -------------------------------------------------------------------------------


def _rules(ts, emotion):
    return {"ts": ts, "emotion": emotion, "confidence": 0.5, "scores": {}}


def test_observed_emotions_is_time_weighted_and_ignores_unknown():
    rules = [_rules(0.0, "unknown"), _rules(2.0, "excited"), _rules(5.0, "relaxed"), _rules(6.0, "excited")]
    obs = precompute_events.observed_emotions(rules, duration_s=10.0)
    assert obs["seconds"] == {"unknown": 2.0, "excited": 7.0, "relaxed": 1.0}
    assert obs["dominant"] == "excited"
    assert precompute_events.observed_emotions([_rules(0.0, "unknown")], 3.0)["dominant"] == "unknown"
    assert precompute_events.observed_emotions([], 3.0) == {"dominant": "unknown", "seconds": {}}


def test_upsert_keeps_hand_filled_fields(tmp_path):
    path = tmp_path / "manifest.json"
    manifest = precompute_events.load_manifest(path)
    entry = precompute_events.upsert(manifest, "eating")
    assert entry["expected_emotion"] is None and entry["treats"] == []
    entry.update(expected_emotion="happy", treats=[3.0], notes="bowl")
    precompute_events.save_manifest(path, manifest)

    again = precompute_events.load_manifest(path)
    same = precompute_events.upsert(again, "eating")
    assert (same["expected_emotion"], same["treats"], same["notes"]) == ("happy", [3.0], "bowl")
    assert len(again["clips"]) == 1


def test_problems_flags_bad_hand_edits():
    ok = {"expected_emotion": "excited", "treats": [1.0, 4.0], "duration_s": 5.0}
    assert precompute_events.problems(ok) == []
    bad = {"expected_emotion": "sad", "treats": [9.0, -1], "duration_s": 5.0}
    assert len(precompute_events.problems(bad)) == 3


def test_read_events_validates_against_the_contracts(tmp_path):
    good = tmp_path / "good.jsonl"
    good.write_text("\n".join(json.dumps(r) for r in [
        {"type": "audio", "data": {"ts": 1.0, "label": "bark", "score": 0.8}},
        {"type": "treat", "data": {"ts": 2.0}},
        {"type": "rules", "data": {**_rules(1.0, "happy"), "scores": {e: 0.1 for e in
                                                                       ("happy", "excited", "relaxed", "anxious", "fearful",
                                                                        "aggressive", "disinterested", "unknown")}}},
    ]) + "\n")
    rules, counts = precompute_events.read_events(good)
    assert counts == {"frame": 0, "audio": 1, "rules": 1, "treat": 1} and len(rules) == 1

    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"type": "audio", "data": {"ts": 1.0, "label": "meow", "score": 0.8}}) + "\n")
    with pytest.raises(ValueError):
        precompute_events.read_events(bad)


@needs_ffmpeg
def test_manifest_only_run_syncs_clips_without_models(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    _make_video(clips / "a.mp4", seconds=3)
    manifest = tmp_path / "manifest.json"
    precompute_events.main(["--clips-dir", str(clips), "--manifest", str(manifest), "--manifest-only"])
    entry = json.loads(manifest.read_text())["clips"][0]
    assert entry["name"] == "a" and entry["clip"] == "clips/a.mp4" and abs(entry["duration_s"] - 3.0) < 0.3
    assert "events" not in entry and entry["expected_emotion"] is None


# -- sources_stub ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name,host", [
    ("pexels-anna-shvets-4588047 (1080p).mp4", "pexels.com/video/4588047/"),
    ("pexels_videos_2022395.mp4", "pexels.com/video/2022395/"),
    ("12345-dog-eating_1080p.mp4", "pixabay.com/videos/id-12345/"),
])
def test_infer_source_from_download_names(name, host):
    url, licence = sources_stub.infer(name)
    assert host in url and "verify" in url and "License" in licence


def test_infer_leaves_unknown_names_as_todo():
    assert sources_stub.infer("my_dog.mp4") == ("TODO", "TODO")


def test_stub_adds_only_unlisted_files_to_the_first_table(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for n in ("old.mp4", "pexels_videos_2022395.mp4", "notes.txt"):
        (raw / n).write_bytes(b"")
    sources = tmp_path / "SOURCES.md"
    sources.write_text("# S\n\n| File | URL | Licence | Shows |\n|---|---|---|---|\n| raw/old.mp4 | u | l | s |\n\n## Other\n\n| A | B |\n|---|---|\n| x | y |\n")
    rows = sources_stub.missing_rows(raw, sources.read_text())
    assert len(rows) == 1 and "pexels_videos_2022395.mp4" in rows[0] and "TODO" in rows[0]
    out = sources_stub.insert_rows(sources.read_text(), rows)
    lines = out.splitlines()
    assert lines.index(rows[0]) == lines.index("| raw/old.mp4 | u | l | s |") + 1
    assert "## Other" in out and sources_stub.missing_rows(raw, out) == []
