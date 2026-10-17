"""One rate limiter (H4): intervals, sliding windows, failure lockout, pruning, and the HTTP lockout
on /pair, /auth/recover and /join. Every time-dependent check runs on an injected fake clock."""

import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.limits import RateLimiter
from backend.web.settings import load_config


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


# -- intervals ----------------------------------------------------------------------------------
def test_check_interval_accepts_the_first_call_then_waits_out_the_interval():
    clock = FakeClock()
    rl = RateLimiter(clock)
    assert rl.check_interval("k", 2.0) == 0.0
    clock.advance(0.5)
    assert rl.check_interval("k", 2.0) == pytest.approx(1.5)  # refused, not recorded
    clock.advance(1.0)  # 1.5 s since the accepted call: still inside the interval
    assert rl.check_interval("k", 2.0) == pytest.approx(0.5)
    clock.advance(0.5)  # 2.0 s since the accepted call
    assert rl.check_interval("k", 2.0) == 0.0


def test_check_interval_keys_are_independent():
    clock = FakeClock()
    rl = RateLimiter(clock)
    assert rl.check_interval("a", 5.0) == 0.0
    assert rl.check_interval("b", 5.0) == 0.0


# -- sliding windows ----------------------------------------------------------------------------
def test_hit_allows_the_limit_inside_the_window_and_recovers_as_it_slides():
    clock = FakeClock()
    rl = RateLimiter(clock)
    assert [rl.hit("ip", 3, 60) for _ in range(3)] == [True, True, True]
    assert rl.hit("ip", 3, 60) is False
    clock.advance(59.0)
    assert rl.hit("ip", 3, 60) is False  # the first hit is still inside the minute
    clock.advance(1.5)  # the oldest hits have slid out
    assert rl.hit("ip", 3, 60) is True


def test_hit_counts_denied_attempts_by_default_so_hammering_stays_blocked():
    clock = FakeClock()
    rl = RateLimiter(clock)
    for _ in range(2):
        rl.hit("ip", 2, 60)
    for _ in range(5):  # denied, but still counted: the window never frees up while hammering
        clock.advance(20.0)
        assert rl.hit("ip", 2, 60) is False
    clock.advance(20.0)  # 60 s after the last allowed hit, the denied ones are still at most 20 s old
    assert rl.hit("ip", 2, 60) is False


def test_hit_count_denied_false_only_counts_accepted_attempts():
    clock = FakeClock()
    rl = RateLimiter(clock)
    assert rl.hit("create", 1, 3600, count_denied=False) is True
    for _ in range(5):
        clock.advance(600.0)
        assert rl.hit("create", 1, 3600, count_denied=False) is False
    # the accepted hit was at t0; 3600 s later it has expired, the denied ones never extended it
    clock.advance(3600.0 - 3000.0 + 1.0)
    assert rl.hit("create", 1, 3600, count_denied=False) is True


# -- failure lockout ----------------------------------------------------------------------------
def test_ten_failures_in_ten_minutes_lock_for_five_minutes():
    clock = FakeClock()
    rl = RateLimiter(clock)
    for _ in range(9):
        rl.record_failure("pair:ip")
    assert rl.locked_for("pair:ip") == 0.0
    rl.record_failure("pair:ip")
    assert rl.locked_for("pair:ip") == pytest.approx(300.0)
    clock.advance(120.0)
    assert rl.locked_for("pair:ip") == pytest.approx(180.0)
    clock.advance(180.0)
    assert rl.locked_for("pair:ip") == 0.0


def test_failures_spread_over_more_than_ten_minutes_do_not_lock():
    clock = FakeClock()
    rl = RateLimiter(clock)
    for _ in range(12):
        rl.record_failure("k")
        clock.advance(70.0)  # 12 failures over 770 s: never ten inside one ten-minute window
    assert rl.locked_for("k") == 0.0


def test_repeated_lockouts_double_up_to_one_hour():
    clock = FakeClock()
    rl = RateLimiter(clock)
    expected = [300.0, 600.0, 1200.0, 2400.0, 3600.0, 3600.0]
    for want in expected:
        for _ in range(10):
            rl.record_failure("k")
        assert rl.locked_for("k") == pytest.approx(want)
        clock.advance(want)
        assert rl.locked_for("k") == 0.0


def test_a_success_clears_failures_and_the_doubling():
    clock = FakeClock()
    rl = RateLimiter(clock)
    for _ in range(10):
        rl.record_failure("k")
    clock.advance(300.0)
    for _ in range(10):
        rl.record_failure("k")
    assert rl.locked_for("k") == pytest.approx(600.0)  # the second lockout doubles
    clock.advance(600.0)
    rl.record_success("k")
    for _ in range(9):
        rl.record_failure("k")
    assert rl.locked_for("k") == 0.0  # the success reset the count
    for _ in range(1):
        rl.record_failure("k")
    assert rl.locked_for("k") == pytest.approx(300.0)  # and the doubling


def test_a_success_on_one_key_does_not_touch_another():
    clock = FakeClock()
    rl = RateLimiter(clock)
    for _ in range(10):
        rl.record_failure("a")
        rl.record_failure("b")
    rl.record_success("a")
    assert rl.locked_for("a") == 0.0
    assert rl.locked_for("b") == pytest.approx(300.0)


def test_retry_after_is_whole_seconds_rounded_up_and_at_least_one():
    assert RateLimiter.retry_after(299.01) == "300"
    assert RateLimiter.retry_after(0.2) == "1"
    assert RateLimiter.retry_after(300.0) == "300"


# -- pruning --------------------------------------------------------------------------------------
def test_the_key_table_stays_bounded_under_a_flood_of_distinct_keys():
    clock = FakeClock()
    rl = RateLimiter(clock, max_keys=100)
    for i in range(5000):  # every key is live (locked or inside a window): the hard bound still holds
        rl.hit(f"ip:{i}", 10, 3600)
        clock.advance(0.001)
    assert len(rl) <= 100


def test_expired_keys_are_pruned_before_live_ones():
    clock = FakeClock()
    rl = RateLimiter(clock, max_keys=100)
    for i in range(100):
        rl.check_interval(f"old:{i}", 1.0)
    clock.advance(10.0)  # every old key's interval is over
    for i in range(50):
        rl.record_failure(f"fail:{i}")  # the table is full: this triggers pruning
    assert len(rl) <= 100
    assert rl.locked_for("missing") == 0.0
    # the live failure counters survive: they are not expired
    rl.record_failure("fail:0")
    assert len(rl) < 100


def test_the_default_bound_is_4096_keys():
    clock = FakeClock()
    rl = RateLimiter(clock)
    for i in range(4200):
        rl.hit(f"ip:{i}", 5, 60)
    assert len(rl) <= 4096


# -- HTTP: lockout on /pair, /auth/recover and /join ---------------------------------------------
def _spki_b64():
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.public_key().public_bytes(serialization.Encoding.DER,
                                        serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


class NoLLM:
    enabled = False
    last_call = None


def households_app(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["auth"]["mode"] = "households"
    cfg["web"]["pipeline"] = "remote"
    return create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


def _use_fake_clock(app_) -> FakeClock:
    clock = FakeClock(t=50_000.0)
    app_.state.rt.clock = clock  # the limiter reads the runtime clock on every call
    return clock


def test_pair_lockout_returns_429_with_retry_after_and_no_oracle(tmp_path):
    app_ = households_app(tmp_path)
    with TestClient(app_) as c:
        _use_fake_clock(app_)
        c.post("/households", json={"dog_name": "Rex"})
        code = c.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        c.cookies.clear()
        bad = {"code": "0102030405060708", "pubkey_spki_b64": _spki_b64(), "device_name": "x"}
        for _ in range(10):
            assert c.post("/pair", json=bad).status_code == 400
        locked = c.post("/pair", json={"code": code, "pubkey_spki_b64": _spki_b64(), "device_name": "x"})
        assert locked.status_code == 429
        assert locked.headers["Retry-After"] == "300"
        assert locked.json()["detail"] == "too many failed attempts; try again later"
        # the right code is refused too while locked: the lockout never reveals the code's validity
        assert c.post("/pair", json={"code": code, "pubkey_spki_b64": _spki_b64(),
                                     "device_name": "x"}).status_code == 429


def test_pair_lockout_doubles_and_a_success_resets_it(tmp_path):
    app_ = households_app(tmp_path)
    with TestClient(app_) as c:
        clock = _use_fake_clock(app_)
        c.post("/households", json={"dog_name": "Rex"})
        code = c.post("/households/me/pairing", json={"kind": "station"}).json()["code"]
        c.cookies.clear()
        bad = {"code": "0102030405060708", "pubkey_spki_b64": _spki_b64(), "device_name": "x"}
        for _ in range(9):
            c.post("/pair", json=bad)
        clock.advance(61.0)  # the per-minute window is past; still below the lockout threshold
        c.post("/pair", json=bad)  # the 10th failure in the ten-minute window locks for 300 s
        clock.advance(300.0)  # lock over
        good = {"code": code, "pubkey_spki_b64": _spki_b64(), "device_name": "Pixel"}
        for _ in range(10):  # the tenth locks for 2 x 300 s, at this same instant
            c.post("/pair", json=bad)
        r = c.post("/pair", json=bad)
        assert r.status_code == 429 and r.headers["Retry-After"] == "600"  # the second lockout doubles
        clock.advance(600.0)
        assert c.post("/pair", json=good).status_code == 200  # a valid code succeeds after the lock


def test_recover_lockout_is_per_ip_and_a_success_resets_it(tmp_path):
    app_ = households_app(tmp_path)
    with TestClient(app_) as c:
        _use_fake_clock(app_)
        key = c.post("/households", json={"dog_name": "Rex"}).json()["recovery_key"]
        c.cookies.clear()
        for _ in range(9):
            assert c.post("/auth/recover", json={"recovery_key": "wrong"}).status_code == 401
        assert c.post("/auth/recover", json={"recovery_key": key}).status_code == 200  # success resets
        c.cookies.clear()
        for _ in range(9):
            assert c.post("/auth/recover", json={"recovery_key": "wrong"}).status_code == 401
        assert c.post("/auth/recover", json={"recovery_key": "wrong"}).status_code == 401  # 10th: locks now
        r = c.post("/auth/recover", json={"recovery_key": key})
        assert r.status_code == 429
        assert r.headers["Retry-After"] == "300"
        assert r.json()["detail"] == "too many failed attempts; try again later"


def test_join_lockout_returns_429_with_retry_after(tmp_path):
    app_ = households_app(tmp_path)
    with TestClient(app_) as c:
        _use_fake_clock(app_)
        for _ in range(10):
            assert c.post("/join", json={"code": "nope", "label": "v"}).status_code == 400
        r = c.post("/join", json={"code": "nope", "label": "v"})
        assert r.status_code == 429 and r.headers["Retry-After"] == "300"
        assert r.json()["detail"] == "too many failed attempts; try again later"
