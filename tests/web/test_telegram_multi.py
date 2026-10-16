from fastapi.testclient import TestClient

from backend.main import create_app
from backend.store import repo as store_repo


def test_telegram_link_and_subscriber_lifecycle(tmp_path):
    app = create_app()
    with TestClient(app) as client:
        # 1. Create a link code
        r = client.post("/telegram/link-code")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        code = data["code"]
        assert "link_" + code in data["link"]

        # 2. Simulate Telegram Webhook sending /start link_<code>
        fake_webhook_start = {
            "update_id": 10001,
            "message": {
                "message_id": 55,
                "text": f"/start link_{code}",
                "chat": {
                    "id": -100987654321,
                    "type": "group",
                    "title": "Dog Parents Family",
                },
                "from": {
                    "id": 123456,
                    "first_name": "Amit",
                    "username": "amit_paw",
                },
            },
        }
        r_hook = client.post("/telegram/webhook", json=fake_webhook_start)
        assert r_hook.status_code == 200
        assert r_hook.json()["ok"] is True
        assert r_hook.json().get("connected") is True

        # 3. Verify subscriber is listed in GET /telegram/subscribers
        r_subs = client.get("/telegram/subscribers")
        assert r_subs.status_code == 200
        subs = r_subs.json()["subscribers"]
        assert len(subs) >= 1
        sub = next(s for s in subs if s["chat_id"] == "-100987654321")
        assert sub["chat_type"] == "group"
        assert sub["title"] == "Dog Parents Family"
        assert sub["label"] == "Amit"

        # 4. Attempting to consume same link again should report expired
        r_hook_again = client.post("/telegram/webhook", json=fake_webhook_start)
        assert r_hook_again.status_code == 200
        assert r_hook_again.json().get("error") == "expired"

        # 5. Simulate interactive callback query for treat
        fake_cb_treat = {
            "update_id": 10002,
            "callback_query": {
                "id": "cb_999",
                "data": "treat:default",
                "message": {
                    "message_id": 56,
                    "chat": {"id": -100987654321},
                    "caption": "🐾 Bruno barked!",
                },
                "from": {"first_name": "Amit"},
            },
        }
        r_cb = client.post("/telegram/webhook", json=fake_cb_treat)
        assert r_cb.status_code == 200
        assert r_cb.json()["action"] == "treat"

        # 6. Simulate interactive callback query for cue
        fake_cb_cue = {
            "update_id": 10003,
            "callback_query": {
                "id": "cb_1000",
                "data": "cue:squeak:default",
                "message": {
                    "message_id": 56,
                    "chat": {"id": -100987654321},
                    "caption": "🐾 Bruno barked!",
                },
                "from": {"first_name": "Amit"},
            },
        }
        r_cue = client.post("/telegram/webhook", json=fake_cb_cue)
        assert r_cue.status_code == 200
        assert r_cue.json()["action"] == "cue:squeak"

        # 7. Unlink / delete subscriber
        r_del = client.delete(f"/telegram/subscribers/{sub['id']}")
        assert r_del.status_code == 200
        assert r_del.json()["ok"] is True

        # Verify list is empty
        r_subs_after = client.get("/telegram/subscribers")
        assert not any(s["id"] == sub["id"] for s in r_subs_after.json()["subscribers"])
