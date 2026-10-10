"""T3.1 policy plumbing: the phone reports its cap, the server sends one `policy` downlink.

The station is authoritative: it reports `cap` (what the person holding the phone allows) and the
server never assumes more exposure than that. Legacy `privacy` downlinks still go out so old APKs
keep working.
"""

import json

import pytest

from backend.web.ingest_events import KIND_PREVIEW, RemoteSession
from backend.web.modes import DEFAULT_PREVIEW, ModeError
from backend.web.remote_pipeline import RemotePipeline
from backend.web.settings import load_config


def cfg(tmp_path):
    c = load_config(tmp_path / "none.yaml", env={})
    c["web"]["pipeline"] = "remote"
    c["web"]["log_dir"] = str(tmp_path / "out")
    return c


def session(tmp_path, **kw):
    """A RemoteSession wired to a fake runtime/pipeline; only handle_text is exercised."""
    from backend.web.ingest_events import RemoteHello

    hello = RemoteHello(device="Phone", proto=2, profile={}, models={}, phone_time=0.0)
    fake = type("RT", (), {"on_frame": lambda *a: None, "on_audio": lambda *a: None,
                           "on_rules": lambda *a: None})()
    return RemoteSession(hello, object(), fake, clock=kw.pop("clock", lambda: 1000.0), **kw)


# -- session: mode_state -------------------------------------------------------------------------

def test_mode_state_is_accepted_and_recorded(tmp_path):
    s = session(tmp_path)
    assert s.station_cap == "full", "an un-upgraded station is assumed to allow full until it says otherwise"
    assert s.effective_mode is None
    assert s.handle_text(json.dumps({"type": "mode_state",
                                     "data": {"cap": "privacy", "effective": "privacy", "reason": "station"}})) == "mode_state"
    assert s.station_cap == "privacy"
    assert s.effective_mode == "privacy"


def test_mode_state_appears_in_snapshot_and_metrics(tmp_path):
    s = session(tmp_path)
    s.handle_text(json.dumps({"type": "mode_state",
                              "data": {"cap": "limited", "effective": "privacy", "reason": "owner"}}))
    snap = s.snapshot(1000.0)
    assert snap["station_cap"] == "limited" and snap["effective_mode"] == "privacy"
    met = s.metrics(1000.0)
    assert met["station_cap"] == "limited" and met["effective_mode"] == "privacy"


@pytest.mark.parametrize("data", [
    {"cap": "ultra", "effective": "full"},      # unknown cap
    {"cap": "full", "effective": "nope"},       # unknown effective
    {"cap": 7, "effective": "full"},            # wrong type
    {},                                         # missing keys
])
def test_unknown_mode_state_is_dropped_not_guessed(tmp_path, data):
    s = session(tmp_path)
    before = s.dropped
    assert s.handle_text(json.dumps({"type": "mode_state", "data": data})) is None
    assert s.dropped == before + 1
    assert s.station_cap == "full", "a bad mode_state must not change the cap"


# -- pipeline: set_policy -------------------------------------------------------------------------

class Downlinks:
    """Captures every downlink the pipeline posts, in order."""

    def __init__(self):
        self.sent = []

    def post(self, msg):
        self.sent.append(msg)
        return True


def pipeline_with_downlinks(tmp_path):
    p = RemotePipeline(cfg(tmp_path))
    cap = Downlinks()
    p._post = cap.post          # type: ignore[method-assign]
    return p, cap


def test_set_policy_posts_policy_then_legacy_privacy(tmp_path):
    p, cap = pipeline_with_downlinks(tmp_path)
    assert p.set_policy("limited") is True
    types = [m["type"] for m in cap.sent]
    assert types == ["policy", "privacy"], "the legacy downlink goes second so new APKs act on policy first"
    policy = cap.sent[0]
    assert policy["data"]["mode"] == "limited"
    assert policy["data"]["preview"] == DEFAULT_PREVIEW
    assert policy["data"]["audio"] is False
    assert policy["data"]["viewers"] == {"video": 0, "audio": 0}
    assert cap.sent[1] == {"type": "privacy", "data": {"enabled": False}}


def test_set_policy_blackout_sends_legacy_enabled_true(tmp_path):
    p, cap = pipeline_with_downlinks(tmp_path)
    p.set_policy("blackout")
    assert cap.sent[1] == {"type": "privacy", "data": {"enabled": True}}


def test_set_policy_carries_viewer_counts_and_preview(tmp_path):
    p, cap = pipeline_with_downlinks(tmp_path)
    p.set_policy("full", preview={"fps": 4, "max_edge": 480, "quality": 70}, audio=True,
                 viewers={"video": 2, "audio": 1})
    d = cap.sent[0]["data"]
    assert d["preview"] == {"fps": 4, "max_edge": 480, "quality": 70}
    assert d["audio"] is True
    assert d["viewers"] == {"video": 2, "audio": 1}


def test_set_policy_rejects_an_unknown_mode(tmp_path):
    p, cap = pipeline_with_downlinks(tmp_path)
    with pytest.raises(ModeError):
        p.set_policy("ultra")
    assert cap.sent == [], "a rejected mode must never reach the phone"


def test_policy_is_the_only_new_downlink_when_no_phone(tmp_path):
    """No phone attached: set_policy still records the policy but reports not-sent."""
    p = RemotePipeline(cfg(tmp_path))  # no _post override -> no phone
    p._session = None
    assert p.set_policy("privacy") is False


def test_get_privacy_reflects_effective_blackout(tmp_path):
    p, _ = pipeline_with_downlinks(tmp_path)
    p.set_policy("full")
    assert p.get_privacy() is False
    p.set_policy("blackout")
    assert p.get_privacy() is True
    p.set_policy("privacy")
    assert p.get_privacy() is False, "only blackout is the total mute"


def test_status_reports_station_cap_and_effective_mode(tmp_path):
    p, _ = pipeline_with_downlinks(tmp_path)
    p.set_policy("limited")
    p._station_cap = "privacy"          # as if the phone reported a lower cap
    st = p.status()
    assert st["station_cap"] == "privacy"
    assert st["effective_mode"] == "privacy"
    assert st["policy_mode"] == "limited"


def test_effective_mode_is_the_minimum_of_cap_and_policy(tmp_path):
    p, _ = pipeline_with_downlinks(tmp_path)
    p._station_cap = "blackout"
    p.set_policy("full")
    assert p.effective_mode == "blackout", "the station cap always wins over the household policy"
    p._station_cap = "full"
    assert p.effective_mode == "full"