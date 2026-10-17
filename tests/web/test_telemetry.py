from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from backend.web.ingest_events import RemoteHello, RemoteSession
from backend.web.remote_pipeline import RemotePipeline


def make_session(clock: float = 1700000000.0) -> RemoteSession:
    hello = RemoteHello(
        device="TestPhone",
        phone_time=clock,
        models={"detector": 1},
        profile="mobile",
        proto=2,
        token="test-token",
        station_id="station-1",
    )
    runtime = MagicMock()
    pipeline = MagicMock()
    return RemoteSession(
        hello=hello,
        pipeline=pipeline,
        runtime=runtime,
        clock=lambda: clock,
    )


def test_telemetry_valid_envelope():
    session = make_session()
    telemetry_data = {
        "ts": 1700000000.0,
        "battery_pct": 85,
        "charging": True,
        "plugged": "usb",
        "temp_c": 32.5,
        "thermal_status": 0,
        "thermal_headroom": None,
        "inference_ms": {"detector": 12.5, "pose": 18.2, "face": 8.0, "yamnet": 3.1},
        "preview_kbps": 48.0,
        "preview_fps": 3.0,
        "preview_dropped": 0,
        "ws_rtt_ms": 45,
        "queue_bytes": 0,
        "spool_bytes": 0,
        "mem_mb": 256,
        "degrade_level": "L0_NORMAL",
    }
    payload = json.dumps({"type": "telemetry", "data": telemetry_data})
    kind = session.handle_text(payload)
    assert kind == "telemetry"
    assert session.dropped == 0
    assert session.latest_telemetry is not None
    assert session.latest_telemetry["battery_pct"] == 85
    assert session.latest_telemetry["temp_c"] == 32.5
    assert len(session.telemetry_history) == 1


def test_telemetry_rejects_out_of_range_battery():
    session = make_session()
    # battery > 100
    msg = json.dumps({"type": "telemetry", "data": {"battery_pct": 101}})
    assert session.handle_text(msg) is None
    assert session.dropped == 1

    # battery < 0
    msg = json.dumps({"type": "telemetry", "data": {"battery_pct": -5}})
    assert session.handle_text(msg) is None
    assert session.dropped == 2

    # battery boolean
    msg = json.dumps({"type": "telemetry", "data": {"battery_pct": True}})
    assert session.handle_text(msg) is None
    assert session.dropped == 3

    # battery string
    msg = json.dumps({"type": "telemetry", "data": {"battery_pct": "85"}})
    assert session.handle_text(msg) is None
    assert session.dropped == 4


def test_telemetry_rejects_out_of_range_temperature():
    session = make_session()
    # temp > 90
    msg = json.dumps({"type": "telemetry", "data": {"temp_c": 95.0}})
    assert session.handle_text(msg) is None
    assert session.dropped == 1

    # temp < -20
    msg = json.dumps({"type": "telemetry", "data": {"temp_c": -25.0}})
    assert session.handle_text(msg) is None
    assert session.dropped == 2

    # temp boolean
    msg = json.dumps({"type": "telemetry", "data": {"temp_c": False}})
    assert session.handle_text(msg) is None
    assert session.dropped == 3


def test_telemetry_rejects_out_of_range_inference_ms():
    session = make_session()
    # inference_ms > 60000
    msg = json.dumps({"type": "telemetry", "data": {"inference_ms": {"detector": 65000.0}}})
    assert session.handle_text(msg) is None
    assert session.dropped == 1

    # inference_ms negative
    msg = json.dumps({"type": "telemetry", "data": {"inference_ms": {"detector": -1.0}}})
    assert session.handle_text(msg) is None
    assert session.dropped == 2

    # inference_ms boolean
    msg = json.dumps({"type": "telemetry", "data": {"inference_ms": {"detector": True}}})
    assert session.handle_text(msg) is None
    assert session.dropped == 3


def test_telemetry_ring_buffer_bounded():
    session = make_session()
    for i in range(400):
        msg = json.dumps({"type": "telemetry", "data": {"battery_pct": 50, "seq": i}})
        assert session.handle_text(msg) == "telemetry"

    assert len(session.telemetry_history) == 360
    assert session.telemetry_history[0]["seq"] == 40
    assert session.telemetry_history[-1]["seq"] == 399


def test_snapshot_and_pipeline_status():
    session = make_session()
    telemetry_data = {
        "battery_pct": 72,
        "charging": False,
        "temp_c": 30.2,
    }
    session.handle_text(json.dumps({"type": "telemetry", "data": telemetry_data}))

    snap = session.snapshot(1700000000.0)
    assert snap["battery_pct"] == 72
    assert snap["charging"] is False
    assert snap["temp_c"] == 30.2
    assert snap["latest_telemetry"] is not None

    metrics = session.metrics(1700000000.0)
    assert metrics["battery_pct"] == 72
    assert metrics["charging"] is False
    assert metrics["temp_c"] == 30.2

    # Test RemotePipeline status mirroring
    pipeline = RemotePipeline(config={"web": {"remote": {}}})
    pipeline.attach(session, send=MagicMock())
    st = pipeline.status()
    assert st["battery_pct"] == 72
    assert st["charging"] is False
    assert st["temp_c"] == 30.2
