import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from check_integration import analyse


def _env(t, ts, data=None, meta=None):
    m = {"type": t, "seq": 1, "ts": ts, "data": data or {}}
    if meta:
        m["meta"] = meta
    return m


def _frame(ts):
    return _env("frame", ts, {"ts": ts, "source": "live", "dog_detected": True, "body_keypoints": {}, "features": {}})


def _rules(ts):
    return _env("rules", ts, {"ts": ts, "emotion": "relaxed", "confidence": 0.6,
                              "scores": {"relaxed": 0.6, "happy": 0.1}})


def test_analyse_pass():
    now = time.time()
    msgs = [_frame(now - 2 + i * 0.25) for i in range(8)] + [_rules(now - 2 + i * 0.25) for i in range(8)]
    msgs += [_env("audio", now - 1, {"ts": now - 1, "label": "bark", "score": 0.9}),
             _env("emotion", now, {"ts": now, "emotion": "relaxed", "confidence": 0.6,
                                   "source": "rules", "reason": "r"})]
    rep = analyse(msgs, {"pipeline": "real", "fps": 8.0}, now)
    assert rep["ok"] and rep["invalid"] == 0 and rep["rates"]["frame"] > 0
    assert rep["max_latency_s"] < 5.0


def test_analyse_fails_on_bad_contract_and_silence():
    rep = analyse([_env("frame", 1.0, {"ts": 1.0, "source": "live"})], {"pipeline": "real"}, time.time())
    assert not rep["ok"] and rep["invalid"] == 1
    rep2 = analyse([], {"pipeline": "real"}, time.time())
    assert not rep2["ok"] and "no messages" in " ".join(rep2["problems"]).lower()
