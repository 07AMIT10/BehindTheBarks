"""Data erasure (households mode): deleting one household removes only that household's data.

The event log root (web.log_dir) holds every household's directory and the flow-test database, so an
erasure that removes the root takes everything else with it.
"""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def _spki_b64() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return base64.b64encode(key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()


def _app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["database_url"] = f"sqlite:///{tmp_path / 'out' / 'btb.db'}"
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    return create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def test_deleting_a_household_with_a_paired_station_keeps_everything_else(tmp_path):
    root = tmp_path / "out"
    root.mkdir(parents=True)
    (root / "keep.txt").write_text("not a household's data")
    other = root / "other-household"
    other.mkdir()
    (other / "session-1.jsonl").write_text("{}")

    with TestClient(_app(tmp_path)) as owner:
        created = owner.post("/households", json={"dog_name": "Rex"}).json()
        key = created["recovery_key"]
        code = owner.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        paired = owner.post("/pair", json={"code": code, "pubkey_spki_b64": _spki_b64(), "device_name": "Pixel"})
        assert paired.status_code == 200
        hid_dir = root / created["household_id"]
        hid_dir.mkdir()
        (hid_dir / "session-2.jsonl").write_text("{}")

        # A station that connects with its own id replaces the idle "default" channel (registry.py), and
        # the station's runtime, whose config carries the shared log root, becomes the household runtime.
        lr = owner.app.state.registry.peek(created["household_id"])
        owner.portal.call(lr.channel, "station-live")
        assert lr.runtime.cfg["web"]["log_dir"] == str(root)

        r = owner.request("DELETE", "/households/me", json={"recovery_key": key})
        assert r.status_code == 200

    assert (root / "keep.txt").read_text() == "not a household's data"
    assert (other / "session-1.jsonl").exists()
    assert not hid_dir.exists()
