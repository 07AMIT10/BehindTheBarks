"""The household registry (T2.2): one Runtime per household, `legacy` first.

This task is a pure refactor — no endpoint or schema usage changes — so the suite below checks the
identity, caching, eviction and cfg-isolation guarantees the later phases rely on.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.demo.mock_pipeline import MockPipeline
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.store import db, repo
from backend.web.hub import Hub
from backend.web.registry import DisabledInterpreter, HouseholdRegistry, HouseholdRuntime
from backend.web.runtime import Runtime
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def cfg(tmp_path):
    c = load_config(tmp_path / "none.yaml", env={})
    c["web"]["log_dir"] = str(tmp_path / "out")
    return c


def make_registry(tmp_path, c=None):
    c = c or cfg(tmp_path)
    clock = {"t": 1_000.0}
    reg = HouseholdRegistry(c, clock=lambda: clock["t"], idle_evict_s=600)
    return reg, clock


def legacy_of(reg, c):
    """Wrap a freshly built (injected-style) runtime, exactly like create_app does today."""
    hub = Hub()
    rt = Runtime(c, MockPipeline(c), NoLLM(), DashboardOnlyNotifier(), hub, clock=time_clock())
    lr = reg.use_legacy(rt, hub, rt.pipeline)
    return lr


def time_clock():
    return lambda: 1_000.0


async def test_legacy_identity_and_caching(tmp_path):
    import asyncio

    reg, clock = make_registry(tmp_path)
    c = cfg(tmp_path)
    hub = Hub()

    async def noop():
        return None

    rt = Runtime(c, MockPipeline(c), NoLLM(), DashboardOnlyNotifier(), hub, clock=time_clock())
    rt._tasks = []  # no background loops in the test
    lr = reg.use_legacy(rt, hub, rt.pipeline)
    assert lr.household_id == "legacy"
    assert reg.peek("legacy").runtime is rt
    assert reg.peek("missing") is None
    assert await reg.get("legacy") is lr  # the legacy wrapper is never recreated
    await reg.close()


async def test_get_creates_one_runtime_per_household(tmp_path):
    import asyncio

    reg, clock = make_registry(tmp_path)
    a1 = await reg.get("a")
    a2 = await reg.get("a")
    b = await reg.get("b")
    assert a1 is a2 and a1 is not b
    assert reg.peek("a") is a1
    assert a1.runtime.phone is None
    await reg.close()


async def test_per_household_cfg_is_a_deep_copy(tmp_path):
    reg, _ = make_registry(tmp_path)
    a = await reg.get("a")
    a.runtime.cfg["web"]["profile"]["dog_name"] = "Rex"
    b = await reg.get("b")
    assert b.runtime.cfg["web"]["profile"]["dog_name"] != "Rex"
    assert a.runtime.cfg["web"]["log_dir"] != b.runtime.cfg["web"]["log_dir"]
    await reg.close()


async def test_idle_eviction_stops_and_forgets(tmp_path):
    reg, clock = make_registry(tmp_path)
    a = await reg.get("a")
    clock["t"] += 6_000  # > idle_evict_s, no station and no viewers
    evicted = await reg.evict_idle()
    assert evicted == ["a"]
    assert reg.peek("a") is None
    assert a.runtime._stopped is True
    again = await reg.get("a")
    assert again is not a and again.runtime._stopped is False
    await reg.close()


async def test_concurrent_first_requests_share_one_runtime(tmp_path):
    """Two requests for a household that has no runtime yet both await Runtime.start(); the loser
    must hand back the winner's runtime and stop its own, not overwrite it and leak it."""
    import asyncio

    reg, _clock = make_registry(tmp_path)
    real_create = reg._create

    async def slow_create(hid):  # a real build yields to the loop; make that explicit here
        await asyncio.sleep(0.01)
        return await real_create(hid)

    reg._create = slow_create
    a, b = await asyncio.gather(reg.get("hh-race"), reg.get("hh-race"))
    assert a is b
    assert reg.peek("hh-race") is a
    assert a.runtime._stopped is False


async def test_a_household_with_a_connected_viewer_is_not_evicted(tmp_path):
    """Idle eviction must not stop the runtime under an open /ws viewer: that socket would hang on
    a dead hub while the dashboard shows stale data."""
    reg, clock = make_registry(tmp_path)
    lr = await reg.get("hh-watch")
    q = lr.hub.subscribe(viewer_id="v1", role="viewer")
    clock["t"] += 10_000.0  # far past idle_evict_s
    assert await reg.evict_idle() == []
    assert reg.peek("hh-watch") is lr
    lr.hub.unsubscribe(q)
    assert await reg.evict_idle() == ["hh-watch"]


# -- T4.x: a station channel gets the same interpreter decision as the household runtime ----------
class FakeLLM:
    """Stands in for LLMInterpreter: records the settings it was built with, never calls out."""

    built: list = []

    def __init__(self, settings):
        FakeLLM.built.append(settings)
        self.enabled = True
        self.last_call = None

    async def interpret(self, **_kw):  # pragma: no cover - the registry tests never reach a call
        raise AssertionError("no LLM call may happen in a registry test")


@pytest.fixture()
def households():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
                           future=True)
    db.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture()
def fake_llm(monkeypatch):
    monkeypatch.setattr(FakeLLM, "built", [])
    monkeypatch.setattr("backend.fusion.llm_interpreter.LLMInterpreter", FakeLLM)
    return FakeLLM


def new_household(sm, *, llm_enabled: bool) -> str:
    with sm() as s:
        h, _ = repo.create_household(s, llm_enabled=llm_enabled)
        s.commit()
        return h.id


async def household_and_station_interpreters(tmp_path, sm, *, hid: str, operator_llm: bool):
    c = cfg(tmp_path)
    c["web"]["llm"]["enabled"] = operator_llm
    reg = HouseholdRegistry(c, clock=lambda: 1_000.0, idle_evict_s=600, sessionmaker=sm)
    try:
        lr = await reg.get(hid)
        ch = await lr.channel("station-1")
        return lr.runtime.interpreter, ch.runtime.interpreter
    finally:
        await reg.close()


async def test_default_household_station_channel_is_disabled(tmp_path, households, fake_llm):
    hid = new_household(households, llm_enabled=False)
    room, station = await household_and_station_interpreters(tmp_path, households, hid=hid, operator_llm=False)
    assert isinstance(room, DisabledInterpreter) and isinstance(station, DisabledInterpreter)
    assert fake_llm.built == []


async def test_owner_opt_in_gives_both_the_real_interpreter(tmp_path, households, fake_llm):
    hid = new_household(households, llm_enabled=True)
    room, station = await household_and_station_interpreters(tmp_path, households, hid=hid, operator_llm=True)
    assert isinstance(room, FakeLLM) and isinstance(station, FakeLLM)
    assert len(fake_llm.built) == 2  # one per runtime, each built from that household's cfg


async def test_owner_opt_in_without_operator_switch_stays_disabled(tmp_path, households, fake_llm):
    hid = new_household(households, llm_enabled=True)
    room, station = await household_and_station_interpreters(tmp_path, households, hid=hid, operator_llm=False)
    assert isinstance(room, DisabledInterpreter) and isinstance(station, DisabledInterpreter)
    assert fake_llm.built == []
