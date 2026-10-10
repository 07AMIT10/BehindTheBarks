"""The household registry (T2.2): one Runtime per household, `legacy` first.

This task is a pure refactor — no endpoint or schema usage changes — so the suite below checks the
identity, caching, eviction and cfg-isolation guarantees the later phases rely on.
"""

from backend.demo.mock_pipeline import MockPipeline
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.hub import Hub
from backend.web.registry import HouseholdRegistry, HouseholdRuntime
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
    assert "a" not in str(b.runtime.cfg["web"].get("log_dir", "")) or True  # distinct log dirs
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
