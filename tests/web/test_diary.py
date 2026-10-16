from pathlib import Path
from fastapi.testclient import TestClient

from backend.analytics.diary import build_daily_diary
from backend.main import create_app


def test_build_daily_diary_aggregation():
    spans = [
        {"start": 1713175200.0, "end": 1713178800.0, "emotion": "relaxed", "reason": "Sleeping on the couch", "snapshot": None},
        {"start": 1713178800.0, "end": 1713180600.0, "emotion": "happy", "reason": "Playing with ball", "snapshot": None},
        {"start": 1713180600.0, "end": 1713181200.0, "emotion": "anxious", "reason": "Mailman at door", "snapshot": None},
    ]
    treats = [1713180000.0]

    diary = build_daily_diary(spans, treats=treats, dog_name="Buddy", target_date="2026-10-16")

    assert diary["ok"] is True
    assert diary["dog_name"] == "Buddy"
    assert diary["date"] == "2026-10-16"
    assert diary["happiness_score"] >= 75
    assert diary["buckets"]["rest_minutes"] == 60
    assert diary["buckets"]["play_minutes"] == 30
    assert diary["buckets"]["alert_minutes"] == 10
    assert diary["buckets"]["routine_minutes"] == 2
    assert len(diary["highlights"]) >= 2
    assert any(h["kind"] == "rest" for h in diary["highlights"])
    assert any(h["kind"] == "play" for h in diary["highlights"])


def test_daily_diary_api_endpoint(tmp_path: Path):
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/diary/daily")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert "happiness_score" in data
        assert "buckets" in data
        assert "percentages" in data
        assert "highlights" in data
