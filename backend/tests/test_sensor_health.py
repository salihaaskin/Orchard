"""Checks for sensor fault detection, gap filling and repair (made-up series, no internet)."""
import os

os.environ["DATA_SOURCE"] = "synthetic"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import sensor_health  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
IDX = pd.date_range("2025-01-01", periods=24 * 20, freq="h")


def daily_cycle(offset_f=0.0) -> pd.Series:
    """A smooth day/night temperature curve (°F) with a little hour-to-hour wiggle."""
    hours = np.arange(len(IDX))
    wiggle = 0.3 * np.sin(hours * 1.7)
    return pd.Series(45 + 10 * np.sin(2 * np.pi * (hours % 24 - 9) / 24) + wiggle + offset_f, index=IDX)


def test_linear_and_anchored_fill():
    sensor = pd.Series([40.0, np.nan, np.nan, np.nan, 44.0], index=IDX[:5])
    gap = IDX[1:4]
    assert list(sensor_health.linear_fill(sensor, gap).round(2)) == [41.0, 42.0, 43.0]
    estimate = pd.Series([38.0, 39.0, 40.0, 41.0, 42.0], index=IDX[:5])  # 2 °F low at both edges
    filled = sensor_health.estimate_fill(sensor, estimate, gap, anchored=True)
    assert list(filled.round(2)) == [41.0, 42.0, 43.0]
    assert list(sensor_health.estimate_fill(sensor, estimate, gap, anchored=False)) == [39.0, 40.0, 41.0]


def test_healthy_record_has_no_alarms():
    ref = daily_cycle()
    found = sensor_health.detect_faults(daily_cycle(-1.5), ref)  # a steady 1.5 °F colder spot
    assert found["episodes"] == []


def test_stuck_only_when_neighbors_move():
    ref = daily_cycle()
    sensor = daily_cycle()
    sensor.iloc[200:210] = sensor.iloc[200]  # 10 identical hours across the morning warm-up
    found = sensor_health.detect_faults(sensor, ref)
    assert [e["type"] for e in found["episodes"]] == ["stuck"]

    flat_ref = ref.copy()
    flat_ref.iloc[195:215] = 44.0  # fog: the neighbors are flat too
    calm = sensor_health.detect_faults(sensor, flat_ref)
    assert calm["episodes"] == [] and "fog" in calm["notes"][0]


def test_missing_spike_impossible_and_drift():
    ref = daily_cycle()
    sensor = daily_cycle()
    sensor.iloc[100:108] = np.nan            # 8 h dropout
    sensor.iloc[150] += 25                    # one-hour spike
    sensor.iloc[170] = 200.0                  # impossible value
    sensor.iloc[300:348] += 8 * 9 / 5         # +8 °C for two days, after a week of normal
    kinds = {e["type"] for e in sensor_health.detect_faults(sensor, ref)["episodes"]}
    assert {"missing", "spike", "impossible_value", "drift"} <= kinds


def test_repair_uses_anchored_idw_and_labels_hours():
    ref = daily_cycle(-1.0)
    sensor = daily_cycle()
    faulty = pd.Series(False, index=IDX)
    faulty.iloc[100:106] = True
    sensor.iloc[100:106] = 999.0              # garbage in the faulty hours
    fixed, source = sensor_health.repair(sensor, faulty, {"idw": ref})
    truth = daily_cycle().iloc[100:106]
    assert (fixed.iloc[100:106] - truth).abs().max() < 0.5    # anchoring removes the 1 °F offset
    assert set(source.iloc[100:106]) == {"idw_anchored"}
    assert source.iloc[0] == "sensor"


def test_sensor_health_endpoint():
    r = client.get("/sensor/health", params={"station_id": "SYN-B", "as_of": "2025-01-31"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in ("ok", "alert")
    assert "chill_repaired" in body and body["repair"]["order_tried"][0] == "idw_anchored"
    assert client.get("/sensor/health", params={"station_id": "nope"}).status_code == 404


def test_overlapping_episodes_are_merged():
    merged = sensor_health._merge([
        {"type": "drift", "start": "2025-01-06 03:00:00", "end": "2025-01-07 06:00:00", "hours": 28, "detail": ""},
        {"type": "drift", "start": "2025-01-06 11:00:00", "end": "2025-01-07 17:00:00", "hours": 31, "detail": ""},
        {"type": "spike", "start": "2025-01-06 12:00:00", "end": "2025-01-06 12:00:00", "hours": 1, "detail": ""},
    ])
    assert [(e["type"], e["hours"]) for e in merged] == [("drift", 39), ("spike", 1)]


def test_simulated_fault_is_caught_and_repaired():
    r = client.get("/sensor/health", params={"station_id": "SYN-B", "as_of": "2025-01-31",
                                            "simulate": "stuck", "simulate_start": "2025-01-20T06:00",
                                            "simulate_hours": 8})
    sim = r.json()["simulation"]
    assert sim["caught"] is True
    assert sim["error_after_repair_C"] < sim["error_of_broken_readings_C"]
    assert client.get("/sensor/health", params={"station_id": "SYN-B", "simulate": "melt"}).status_code == 400


def test_estimate_repairs_high_confidence_faults_by_default(monkeypatch):
    """With SENSOR_REPAIR on (default), a high-confidence fault is repaired in /estimate."""
    from app import config, interp, main
    assert config.SENSOR_REPAIR is True
    monkeypatch.setattr(interp, "elevation_m", lambda lat, lon: 40.0)
    calls = {}

    def fake_health(station_id, start, end, *args, **kwargs):
        raw = main.load_hourly([station_id], start, end)[station_id]
        calls["station"] = station_id
        report = {"station_id": station_id, "status": "alert", "alert_confidence": "high",
                  "faulty_hours": 5, "episodes": [{"type": "stuck"}],
                  "repair": {"hours_by_source": {"sensor": len(raw) - 5, "idw_anchored": 5}}}
        return report, raw - 1.0  # a visibly different "repaired" record

    monkeypatch.setattr(main, "station_health", fake_health)
    monkeypatch.setattr(main.source_select, "recommend_by_metric",
                        lambda *a: {"chill_portions": {"method": "nearest", "reason": "test",
                                                       "elevation_rule": None}})
    body = client.get("/estimate", params={"lat": 39.42, "lon": -121.71, "as_of": "2025-01-15",
                                          "history_seasons": 1}).json()
    alerts = body["sensor_alerts"]
    assert alerts["repaired_in_this_estimate"] is True
    assert alerts["repair_hours_by_source"]["idw_anchored"] == 5
    assert body["chill"]["method_details"]["station_id"] == calls["station"]


def test_simulation_returns_chart_series():
    body = client.get("/sensor/health", params={"station_id": "SYN-B", "as_of": "2025-01-31",
                                               "simulate": "drift", "simulate_start": "2025-01-20T06:00",
                                               "simulate_hours": 24}).json()
    s = body["series"]
    assert len(s["time"]) == len(s["reported"]) == len(s["repaired"]) == len(s["true"]) == 73
    assert any(s["faulty"])
