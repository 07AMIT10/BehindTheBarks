"""Unit tests for Cloudflare R2 / S3 snapshot storage and runtime notification integration."""

import base64
from unittest.mock import MagicMock

import pytest

from backend.contracts import EmotionState
from backend.store.storage import EphemeralStorage, R2SnapshotStorage, build_storage
from backend.web.hub import Hub
from backend.web.runtime import Runtime


@pytest.mark.asyncio
async def test_ephemeral_storage_returns_base64_data_uri():
    storage = EphemeralStorage()
    fake_jpeg = b"\xff\xd8\xff\xe0fake_jpeg_content\xff\xd9"
    res = await storage.store_snapshot(fake_jpeg, household_id="house-1", ts=1700000000.0, emotion="happy")
    assert res is not None
    assert res.startswith("data:image/jpeg;base64,")
    encoded = res.split(",", 1)[1]
    assert base64.b64decode(encoded) == fake_jpeg


@pytest.mark.asyncio
async def test_ephemeral_storage_handles_none_gracefully():
    storage = EphemeralStorage()
    res = await storage.store_snapshot(None, household_id="house-1", ts=1700000000.0, emotion="happy")
    assert res is None


@pytest.mark.asyncio
async def test_r2_storage_uploads_and_generates_presigned_url():
    mock_s3 = MagicMock()
    mock_s3.generate_presigned_url.return_value = "https://r2.example.com/snapshots/house-1/photo.jpg?token=abc"

    storage = R2SnapshotStorage(
        account_id="acc123",
        access_key_id="key123",
        secret_access_key="secret123",
        bucket="test-bucket",
        presigned_ttl_s=1800,
        s3_client=mock_s3,
    )

    fake_jpeg = b"\xff\xd8test_bytes\xff\xd9"
    url = await storage.store_snapshot(fake_jpeg, household_id="house-1", ts=1728630000.0, emotion="excited")

    assert url == "https://r2.example.com/snapshots/house-1/photo.jpg?token=abc"
    mock_s3.put_object.assert_called_once()
    call_kwargs = mock_s3.put_object.call_args[1]
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Body"] == fake_jpeg
    assert call_kwargs["ContentType"] == "image/jpeg"
    assert call_kwargs["Key"].startswith("snapshots/house-1/1728630000_")
    assert call_kwargs["Key"].endswith("_excited.jpg")

    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": call_kwargs["Key"]},
        ExpiresIn=1800,
    )


@pytest.mark.asyncio
async def test_r2_storage_falls_back_to_base64_on_upload_error():
    mock_s3 = MagicMock()
    mock_s3.put_object.side_effect = RuntimeError("S3 connection timed out")

    storage = R2SnapshotStorage(
        account_id="acc123",
        access_key_id="key123",
        secret_access_key="secret123",
        bucket="test-bucket",
        s3_client=mock_s3,
    )

    fake_jpeg = b"\xff\xd8fallback_content\xff\xd9"
    res = await storage.store_snapshot(fake_jpeg, household_id="house-1", ts=1728630000.0, emotion="anxious")

    assert res is not None
    assert res.startswith("data:image/jpeg;base64,")
    encoded = res.split(",", 1)[1]
    assert base64.b64decode(encoded) == fake_jpeg


def test_build_storage_factory(monkeypatch):
    # Default without env -> EphemeralStorage
    monkeypatch.delenv("BTB_R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    assert isinstance(build_storage({}), EphemeralStorage)

    # With credentials -> R2SnapshotStorage
    monkeypatch.setenv("BTB_R2_ACCOUNT_ID", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("BTB_R2_ACCESS_KEY_ID", "key123")
    monkeypatch.setenv("BTB_R2_SECRET_ACCESS_KEY", "secret123")
    monkeypatch.setenv("BTB_R2_BUCKET", "custom-bucket")
    storage = build_storage({})
    assert isinstance(storage, R2SnapshotStorage)
    assert storage.bucket == "custom-bucket"

    # Malformed account ID -> graceful fallback to EphemeralStorage
    monkeypatch.setenv("BTB_R2_ACCOUNT_ID", "invalid_id_with_underscore")
    fallback = build_storage({})
    assert isinstance(fallback, EphemeralStorage)


@pytest.mark.asyncio
async def test_runtime_notify_integrates_with_storage():
    class DummyNotifier:
        def __init__(self):
            self.sent = []

        async def send(self, state, jpeg):
            self.sent.append((state, jpeg))
            from backend.notify.base import NotifyResult
            return NotifyResult("sent", "dashboard_only")

    class DummyPipeline:
        def latest_frame_jpeg(self):
            return b"\xff\xd8dummy_frame\xff\xd9"

    class MockStorage:
        async def store_snapshot(self, jpeg, *, household_id, ts, emotion):
            return f"https://cdn.example.com/{household_id}/{emotion}.jpg"

    cfg = {
        "web": {
            "mode": "full",
            "log_dir": "out",
            "ws": {"frame_fps": 8, "client_queue": 64, "history": 10},
            "video": {"fps": 8},
            "state": {
                "persist_s": 3.0,
                "no_dog_unknown_s": 2.0,
                "llm_override_conf": 0.7,
                "llm_stale_s": 12.0,
                "cooldown_s": 60.0,
                "cooldowns": {},
                "always_notify": [],
                "quiet": [],
                "tick_hz": 4,
                "live_hz": 1,
            },
            "llm": {
                "min_interval_s": 3.0,
                "heartbeat_s": 10.0,
                "audio_trigger_score": 0.6,
                "timeout_s": 8.0,
                "context_s": 3.0,
                "max_feature_samples": 6,
                "image_max_side": 512,
                "json_mode": True,
                "temperature": 0.2,
                "max_tokens": 200,
            },
            "notify": {"mode": "dashboard_only"},
            "ingest": {
                "stale_s": 5.0,
                "status_every_s": 2.0,
                "fps_window_s": 3.0,
                "max_message_bytes": 2000000,
            },
        }
    }

    notifier = DummyNotifier()
    hub = Hub(client_queue=16, history=16, frame_fps=8)
    q = hub.subscribe(role="owner")

    rt = Runtime(
        cfg=cfg,
        pipeline=DummyPipeline(),
        interpreter=MagicMock(),
        notifier=notifier,
        hub=hub,
        storage=MockStorage(),
    )
    rt.household_id = "test-household-42"

    state = EmotionState(
        ts=1728631234.0,
        emotion="happy",
        confidence=0.88,
        source="rules",
        reason="Good dog",
        snapshot=None,
    )

    await rt._notify(state)

    # 1. Notifier received the state with the custom URL
    assert len(notifier.sent) == 1
    sent_state, sent_jpeg = notifier.sent[0]
    assert sent_state.snapshot == "https://cdn.example.com/test-household-42/happy.jpg"
    assert sent_jpeg == b"\xff\xd8dummy_frame\xff\xd9"

    # 2. Hub published the notification envelope with the URL
    msg = await q.get()
    assert msg["type"] == "notification"
    assert msg["data"]["state"]["snapshot"] == "https://cdn.example.com/test-household-42/happy.jpg"
