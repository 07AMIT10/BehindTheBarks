import httpx

from backend.contracts import EmotionState
from backend.notify import build_notifier, caption_for
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.notify.telegram import TelegramNotifier
from backend.web.settings import load_config

STATE = EmotionState(ts=1727340001.0, emotion="fearful", confidence=0.81, source="fused",
                     reason="Tail tucked, body low, whimpering.")


PROFILE = {"dog_name": "Bruno", "location": "Kitchen", "zone_label": "feeding area"}


def test_caption_matches_design():
    lines = caption_for(STATE, PROFILE).splitlines()
    assert lines[0].endswith("Bruno seems fearful · 81%")
    assert lines[1] == "Tail tucked, body low, whimpering."
    assert lines[2].startswith("Kitchen · feeding area · Fused reading · ")
    happy = STATE.model_copy(update={"emotion": "happy", "source": "llm"})
    first, _, last = caption_for(happy, PROFILE).splitlines()
    assert first.endswith("Bruno is happy · 81%") and "AI reading" in last
    assert "Your dog" in caption_for(STATE)  # no profile


async def test_dashboard_only():
    r = await DashboardOnlyNotifier().send(STATE, None)
    assert (r.status, r.detail) == ("dashboard_only", "would send to owner")


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_telegram_send_photo_ok():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"ok": True})
    r = await TelegramNotifier("T", "42", client=_client(handler)).send(STATE, b"\xff\xd8jpeg")
    assert r.status == "sent" and seen[0].url.path == "/botT/sendPhoto"
    assert b'name="chat_id"' in seen[0].content and b"42" in seen[0].content


async def test_telegram_text_when_no_photo():
    seen = []
    r = await TelegramNotifier("T", "42", client=_client(lambda q: seen.append(q) or httpx.Response(200, json={"ok": True}))).send(STATE, None)
    assert r.status == "sent" and seen[0].url.path == "/botT/sendMessage"


async def test_telegram_retries_once_then_fails_without_raising():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500, json={"ok": False, "description": "boom"})
    r = await TelegramNotifier("T", "42", client=_client(handler)).send(STATE, b"\xff\xd8")
    assert r.status == "failed" and len(calls) == 2


async def test_telegram_network_error_is_failed_result():
    def handler(req):
        raise httpx.ConnectError("offline")
    r = await TelegramNotifier("T", "42", client=_client(handler)).send(STATE, None)
    assert r.status == "failed" and "offline" in r.detail


def test_build_notifier_falls_back(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={"NOTIFY_MODE": "telegram"})
    assert isinstance(build_notifier(cfg, env={}), DashboardOnlyNotifier)  # no token
    assert isinstance(build_notifier(cfg, env={"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1"}), TelegramNotifier)
