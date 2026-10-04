import httpx

from backend.contracts import EmotionState
from backend.notify import (
    DashboardOnlyNotifier,
    MultiChannelNotifier,
    TelegramNotifier,
    WhatsAppNotifier,
    build_notifier,
    caption_for,
)
from backend.web.settings import load_config

STATE = EmotionState(
    ts=1727340001.0,
    emotion="fearful",
    confidence=0.81,
    source="fused",
    reason="Tail tucked, body low, whimpering.",
)

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
    r = await TelegramNotifier(
        "T", "42", client=_client(lambda q: seen.append(q) or httpx.Response(200, json={"ok": True}))
    ).send(STATE, None)
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


async def test_whatsapp_send_photo_ok():
    seen = []

    def handler(req):
        seen.append(req)
        if "/media" in req.url.path:
            return httpx.Response(200, json={"id": "media_999"})
        if "/messages" in req.url.path:
            return httpx.Response(200, json={"messages": [{"id": "wamid.123"}]})
        return httpx.Response(404)

    client = _client(handler)
    wa = WhatsAppNotifier("WA_TOKEN", "12345", "+1 (555) 987-6543", client=client, profile=PROFILE)
    r = await wa.send(STATE, b"\xff\xd8jpeg")
    assert r.status == "sent" and r.channel == "whatsapp"
    assert len(seen) == 2
    assert "/media" in seen[0].url.path
    assert "/messages" in seen[1].url.path
    assert b"media_999" in seen[1].content
    assert b"9876543" in seen[1].content


async def test_whatsapp_text_when_no_photo():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"messages": [{"id": "wamid.text"}]})

    client = _client(handler)
    wa = WhatsAppNotifier("WA_TOKEN", "12345", "15559876543", client=client, profile=PROFILE)
    r = await wa.send(STATE, None)
    assert r.status == "sent"
    assert len(seen) == 1
    assert "/messages" in seen[0].url.path
    assert b'"type": "text"' in seen[0].content or b'"type":"text"' in seen[0].content


async def test_whatsapp_fallback_to_text_when_media_upload_fails():
    seen = []

    def handler(req):
        seen.append(req)
        if "/media" in req.url.path:
            return httpx.Response(500, json={"error": "media failed"})
        return httpx.Response(200, json={"messages": [{"id": "wamid.fallback"}]})

    client = _client(handler)
    wa = WhatsAppNotifier("WA_TOKEN", "12345", "15559876543", client=client)
    r = await wa.send(STATE, b"\xff\xd8jpeg")
    assert r.status == "sent"
    assert len(seen) == 2
    assert "/media" in seen[0].url.path
    assert "/messages" in seen[1].url.path


async def test_whatsapp_retries_once_then_fails():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(500, json={"error": "fatal"})

    client = _client(handler)
    wa = WhatsAppNotifier("WA_TOKEN", "12345", "15559876543", client=client)
    r = await wa.send(STATE, None)
    assert r.status == "failed" and r.channel == "whatsapp"
    assert len(calls) == 2


async def test_whatsapp_network_error():
    def handler(req):
        raise httpx.ConnectError("whatsapp offline")

    client = _client(handler)
    wa = WhatsAppNotifier("WA_TOKEN", "12345", "15559876543", client=client)
    r = await wa.send(STATE, None)
    assert r.status == "failed" and "whatsapp offline" in r.detail


async def test_multi_channel_notifier():
    def tg_handler(req):
        return httpx.Response(200, json={"ok": True})

    def wa_handler(req):
        return httpx.Response(200, json={"messages": [{"id": "1"}]})

    tg = TelegramNotifier("T", "1", client=_client(tg_handler))
    wa = WhatsAppNotifier("W", "2", "3", client=_client(wa_handler))
    multi = MultiChannelNotifier([tg, wa])
    assert multi.is_external

    r = await multi.send(STATE, None)
    assert r.status == "sent"
    assert "telegram" in r.channel and "whatsapp" in r.channel


async def test_multi_channel_notifier_partial_success():
    def tg_handler(req):
        return httpx.Response(500, json={"ok": False})

    def wa_handler(req):
        return httpx.Response(200, json={"messages": [{"id": "1"}]})

    tg = TelegramNotifier("T", "1", client=_client(tg_handler))
    wa = WhatsAppNotifier("W", "2", "3", client=_client(wa_handler))
    multi = MultiChannelNotifier([tg, wa])
    r = await multi.send(STATE, None)
    assert r.status == "sent"
    assert r.channel == "whatsapp"


async def test_multi_channel_notifier_all_fail():
    def fail_handler(req):
        return httpx.Response(500, json={"ok": False})

    tg = TelegramNotifier("T", "1", client=_client(fail_handler))
    multi = MultiChannelNotifier([tg])
    r = await multi.send(STATE, None)
    assert r.status == "failed"
    assert "telegram" in r.detail


def test_build_notifier_falls_back(tmp_path):
    cfg = load_config(tmp_path / "none.yaml", env={"NOTIFY_MODE": "telegram"})
    assert isinstance(build_notifier(cfg, env={}), DashboardOnlyNotifier)  # no token
    assert isinstance(
        build_notifier(cfg, env={"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1"}),
        TelegramNotifier,
    )


def test_build_notifier_whatsapp_and_multi(tmp_path):
    # WhatsApp mode
    cfg_wa = load_config(tmp_path / "none.yaml", env={"NOTIFY_MODE": "whatsapp"})
    assert isinstance(build_notifier(cfg_wa, env={}), DashboardOnlyNotifier)
    wa_env = {
        "NOTIFY_MODE": "whatsapp",
        "WHATSAPP_TOKEN": "tk",
        "WHATSAPP_PHONE_NUMBER_ID": "123",
        "WHATSAPP_RECIPIENT_PHONE": "456",
    }
    assert isinstance(build_notifier(cfg_wa, env=wa_env), WhatsAppNotifier)

    # Multi mode
    cfg_multi = load_config(tmp_path / "none.yaml", env={"NOTIFY_MODE": "multi"})
    multi_env = {
        **wa_env,
        "NOTIFY_MODE": "multi",
        "TELEGRAM_BOT_TOKEN": "T",
        "TELEGRAM_CHAT_ID": "1",
    }
    multi_notif = build_notifier(cfg_multi, env=multi_env)
    assert isinstance(multi_notif, MultiChannelNotifier)
    assert len(multi_notif._notifiers) == 2
