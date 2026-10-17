import struct
from pathlib import Path

from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import VIEWER_COOKIE, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config
from tests.web.test_isolation import Station


class NoLLM:
    enabled = False
    last_call = None


def make_dummy_wav(duration_s: float = 1.0, sample_rate: int = 16000) -> bytes:
    num_samples = int(duration_s * sample_rate)
    pcm_bytes = b"\x00\x00" * num_samples
    subchunk2_size = len(pcm_bytes)
    chunk_size = 36 + subchunk2_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        chunk_size,
        b"WAVE",
        b"fmt ",
        16,  # subchunk1 size
        1,   # PCM format
        1,   # 1 channel (mono)
        sample_rate,
        sample_rate * 2,  # byte rate
        2,   # block align
        16,  # bits per sample
        b"data",
        subchunk2_size,
    )
    return header + pcm_bytes


def test_audio_snippet_upload_and_download(tmp_path: Path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app) as client:
        wav = make_dummy_wav(0.5)

        # Missing header
        r = client.post("/events/audio-snippet", content=wav)
        assert r.status_code == 400

        # Invalid payload (not RIFF)
        r = client.post("/events/audio-snippet", headers={"X-Snippet-ID": "snip_test_1"}, content=b"not a wav file header test")
        assert r.status_code == 400

        # Successful upload
        snip_id = "snip_bark_1001"
        r = client.post("/events/audio-snippet", headers={"X-Snippet-ID": snip_id}, content=wav)
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["snippet_id"] == snip_id

        # Retrieve snippet
        r_get = client.get(f"/snippets/{snip_id}")
        assert r_get.status_code == 200
        assert r_get.headers["content-type"] == "audio/wav"
        assert r_get.content == wav

        # Not found
        r_not_found = client.get("/snippets/non_existent_snip")
        assert r_not_found.status_code == 404


# -- households mode (the authed upload contract; snippets dir is cwd-relative, sandboxed below) ------

def _households_app(tmp_path, *, snippets_cfg: dict | None = None):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    cfg["web"]["video"]["idle_timeout_s"] = 0.5  # a camera-less household must not hang the test
    if snippets_cfg is not None:
        cfg["web"]["snippets"] = snippets_cfg
    return create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def test_households_snippet_upload_needs_a_session_or_a_paired_station(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the endpoint's relative out/snippets stays inside the sandbox
    app = _households_app(tmp_path)
    with TestClient(app) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        owner_cookie = c.cookies[VIEWER_COOKIE]
        wav = make_dummy_wav(0.5)

        # no session and no station identity: 401 before a single body byte is read
        c.cookies.clear()
        assert c.post("/events/audio-snippet", headers={"X-Snippet-ID": "s1"}, content=wav).status_code == 401
        # a garbage session is just as unauthorized
        c.cookies.set(VIEWER_COOKIE, "not-a-session")
        assert c.post("/events/audio-snippet", headers={"X-Snippet-ID": "s1"}, content=wav).status_code == 401

        # the owner's session is a credential: the upload lands in their household's own directory
        c.cookies.set(VIEWER_COOKIE, owner_cookie)
        r = c.post("/events/audio-snippet", headers={"X-Snippet-ID": "snip_owner_1"}, content=wav)
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["snippet_id"] == "snip_owner_1"
        assert (Path("out/snippets") / hid / "snip_owner_1.wav").read_bytes() == wav


def test_a_foreign_station_id_never_writes_into_this_household(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _households_app(tmp_path)
    with TestClient(app) as c:
        hid_a = c.post("/households", json={"dog_name": "A"}).json()["household_id"]
        a_cookie = c.cookies[VIEWER_COOKIE]
        c.cookies.clear()
        c.post("/households", json={"dog_name": "B"})
        station_b = Station(c, b"BMARKER", "PhoneB")
        wav = make_dummy_wav(0.5)

        # household A's owner presenting household B's station id: the station does not belong here
        c.cookies.set(VIEWER_COOKIE, a_cookie)
        r = c.post("/events/audio-snippet", headers={"X-Snippet-ID": "x", "X-Station-ID": station_b.station_id},
                   content=wav)
        assert r.status_code == 403

        # the same station id with NO viewer token is a station-identity credential — for its own
        # household only (Android phones carry no viewer session when paired as households stations)
        c.cookies.clear()
        r = c.post("/events/audio-snippet",
                   headers={"X-Snippet-ID": "snip_b_1", "X-Station-ID": station_b.station_id}, content=wav)
        assert r.status_code == 200
        assert (Path("out/snippets") / station_b.household_id / "snip_b_1.wav").exists()
        assert not (Path("out/snippets") / hid_a).exists()  # A was never written by either attempt


def test_oversize_snippets_are_refused_413_and_never_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _households_app(tmp_path)
    with TestClient(app) as c:
        c.post("/households", json={"dog_name": "Rex"})
        big = b"RIFF" + b"\x00" * 1_000_000  # 1,000,004 bytes: past the 1 MB cap, RIFF-prefixed
        r = c.post("/events/audio-snippet", headers={"X-Snippet-ID": "big"}, content=big)
        assert r.status_code == 413
        assert not Path("out/snippets").exists()  # the body was never buffered to any file

        # a same-shape payload within the cap is still accepted: only the size rule bit us
        wav = make_dummy_wav(0.5)
        assert c.post("/events/audio-snippet", headers={"X-Snippet-ID": "fits"}, content=wav).status_code == 200


def test_snippet_upload_sweeps_the_household_past_its_count_cap(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _households_app(tmp_path, snippets_cfg={"max_per_household": 3, "retention_days": 7})
    with TestClient(app) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        wav = make_dummy_wav(0.5)
        for i in range(5):
            assert c.post("/events/audio-snippet", headers={"X-Snippet-ID": f"snip_{i}"},
                          content=wav).status_code == 200
        files = sorted(p.name for p in (Path("out/snippets") / hid).iterdir())
        assert files == ["snip_2.wav", "snip_3.wav", "snip_4.wav"]  # oldest beyond the cap were swept


def test_snippets_older_than_retention_are_swept_on_the_next_upload(tmp_path, monkeypatch):
    import time

    monkeypatch.chdir(tmp_path)
    # retention_days=0.5/86400 -> a 0.5 s cutoff; the sleep clears it before the "new" upload sweeps
    app = _households_app(tmp_path, snippets_cfg={"max_per_household": 50, "retention_days": 0.5 / 86_400})
    with TestClient(app) as c:
        hid = c.post("/households", json={"dog_name": "Rex"}).json()["household_id"]
        wav = make_dummy_wav(0.5)
        assert c.post("/events/audio-snippet", headers={"X-Snippet-ID": "old"}, content=wav).status_code == 200
        time.sleep(1.0)  # the age cutoff for retention_days=5/86400 (~0.5 s) is now surely met
        r = c.post("/events/audio-snippet", headers={"X-Snippet-ID": "new"}, content=wav)
        assert r.status_code == 200
        hid_dir = Path("out/snippets") / hid
        assert not (hid_dir / "old.wav").exists()
        assert (hid_dir / "new.wav").exists()


def test_household_downloads_stay_inside_the_household_and_private(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = _households_app(tmp_path)
    with TestClient(app) as c:
        hid_a = c.post("/households", json={"dog_name": "A"}).json()["household_id"]
        a_cookie = c.cookies[VIEWER_COOKIE]
        wav = make_dummy_wav(0.5)
        r = c.post("/events/audio-snippet", headers={"X-Snippet-ID": "snip_a_1"}, content=wav)
        assert r.status_code == 200

        # household A reads its own snippet back, privately
        r_get = c.get(f"/snippets/snip_a_1")
        assert r_get.status_code == 200
        assert r_get.headers["content-type"] == "audio/wav"
        assert r_get.headers["Cache-Control"] == "private, max-age=3600"
        assert r_get.content == wav

        # household B, even as owner, cannot read A's snippet: no households fallback exists
        c.cookies.clear()
        c.post("/households", json={"dog_name": "B"})
        assert c.get("/snippets/snip_a_1").status_code == 404

        # ...and a stray pin-mode legacy file is invisible to households mode too
        legacy_dir = Path("out/snippets") / "legacy"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "snip_a_1.wav").write_bytes(wav)
        c.cookies.set(VIEWER_COOKIE, a_cookie)
        assert c.get("/snippets/snip_a_1").status_code == 200  # A still reads its own, not a legacy copy
        c.cookies.clear()
        c.post("/households", json={"dog_name": "C"})
        assert c.get("/snippets/snip_a_1").status_code == 404  # not the legacy fallback copy either
