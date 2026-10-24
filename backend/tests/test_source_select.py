"""Checks for the Source Selection Engine, on a small made-up validation table."""
import os

os.environ["DATA_SOURCE"] = "synthetic"

import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, interp, source_select  # noqa: E402
from app.main import app  # noqa: E402
from app.source_compare import METHODS  # noqa: E402

client = TestClient(app)


def make_table() -> pd.DataFrame:
    """Close, flat stations: nearest wins. Far stations with big elevation gaps: corrected wins."""
    rows = []
    for i in range(10):
        close = i < 5
        mae = ({"nearest": 0.6, "idw": 0.8, "open_meteo": 1.5, "open_meteo_corrected": 0.9} if close else
               {"nearest": 2.0, "idw": 1.8, "open_meteo": 1.9, "open_meteo_corrected": 1.2})
        rows.append({
            "station_id": f"S{i}", "name": f"Station {i}", "season_end": "2025-02-28",
            "nearest_dist_km": 10 + i if close else 60 + i,
            "nearest_elev_diff_m": 5 if close else -250,
            "best_method_by_mae": min(mae, key=mae.get),
            **{f"mae_C_{m}": v for m, v in mae.items()},
            **{f"chill_portions_error_{m}": v * 2 for m, v in mae.items()},
        })
    return pd.DataFrame(rows)


def test_picks_nearest_when_close_and_flat():
    c = source_select.select_source(make_table(), dist_km=12, elev_diff_m=0, k=3)
    assert c["selected_method"] == "nearest"
    assert c["status"] == "validated"
    assert c["expected_error"]["nearest"]["expected_mae_C"] == 0.6


def test_picks_corrected_when_far_and_higher():
    c = source_select.select_source(make_table(), dist_km=62, elev_diff_m=240, k=3)
    assert c["selected_method"] == "open_meteo_corrected"
    assert c["status"] == "validated"


def test_says_insufficient_when_nothing_similar():
    c = source_select.select_source(make_table(), dist_km=200, elev_diff_m=1500, k=3)
    assert c["status"] == "insufficient_validation"
    assert c["selected_method"] == source_select.overall_best(make_table())


def test_selector_beats_every_fixed_method_on_unseen_stations():
    ev = source_select.evaluate_selector(make_table(), k=3)
    sel = ev["strategies"]["selector"]["mean_mae_C"]
    for m in METHODS:
        assert sel < ev["strategies"][f"always_{m}"]["mean_mae_C"]
    assert ev["selector_gain_vs_best_fixed_C"] > 0


def test_source_endpoint_without_table(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALIDATION_TABLE", tmp_path / "missing.csv")
    monkeypatch.setattr(interp, "elevation_m", lambda lat, lon: 40.0)  # no internet in tests
    r = client.get("/source", params={"lat": 39.61, "lon": -121.82, "exclude": ["12"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_validation_table"
    assert body["nearest_station"]["station_id"] != "12"  # excluded, like the Durham demo
    assert body["idw_disagreement_C"] is not None


def test_source_endpoint_with_table(monkeypatch, tmp_path):
    path = tmp_path / "loso.csv"
    make_table().to_csv(path, index=False)
    monkeypatch.setattr(config, "VALIDATION_TABLE", path)
    monkeypatch.setattr(interp, "elevation_m", lambda lat, lon: 40.0)
    body = client.get("/source", params={"lat": 39.61, "lon": -121.82}).json()
    assert body["selected_method"] in METHODS
    assert body["validation_table"]["rows"] == 10


@pytest.mark.parametrize("k", [3, 5])
def test_evaluation_endpoint(monkeypatch, tmp_path, k):
    path = tmp_path / "loso.csv"
    make_table().to_csv(path, index=False)
    monkeypatch.setattr(config, "VALIDATION_TABLE", path)
    ev = client.get("/source/evaluation", params={"k": k}).json()
    assert ev["selector"]["cases"] == 10 and ev["selector"]["stations"] == 10


def _fake_evaluation(gain):
    """Stand-in for evaluate_selector's verdict: selector beat (gain > 0) or lost to always-IDW."""
    return lambda path, mtime: {
        "best_fixed_method": "idw", "selector_gain_vs_best_fixed_C": gain, "cases": 10,
        "strategies": {"selector": {"mean_mae_C": 1.0 - gain}, "always_idw": {"mean_mae_C": 1.0}},
    }


def _use_table(monkeypatch, tmp_path):
    path = tmp_path / "loso.csv"
    make_table().to_csv(path, index=False)
    monkeypatch.setattr(config, "VALIDATION_TABLE", path)
    monkeypatch.setattr(config, "SELECT_K", 3)


def test_recommend_uses_best_single_method_when_selector_failed_check(monkeypatch, tmp_path):
    _use_table(monkeypatch, tmp_path)
    monkeypatch.setattr(source_select, "_evaluation_for", _fake_evaluation(-0.05))
    c = source_select.recommend(dist_km=62, elev_diff_m=240)  # locally, corrected looks best
    assert c["selector_check"]["passed"] is False
    assert c["selected_method"] == "idw"
    assert "did not beat" in c["reason"]


def test_recommend_keeps_local_choice_when_selector_passed_check(monkeypatch, tmp_path):
    _use_table(monkeypatch, tmp_path)
    monkeypatch.setattr(source_select, "_evaluation_for", _fake_evaluation(0.05))
    c = source_select.recommend(dist_km=62, elev_diff_m=240)
    assert c["selector_check"]["passed"] is True
    assert c["selected_method"] == "open_meteo_corrected"


def make_gap_table(n=24) -> pd.DataFrame:
    """IDW error rises with the IDW-vs-Open-Meteo disagreement: mae = 0.5 + 1.0 * gap (+ small wiggle)."""
    t = make_table()
    t = pd.concat([t] * (n // len(t) + 1), ignore_index=True).iloc[:n].copy()
    t["station_id"] = [f"G{i}" for i in range(n)]
    t["idw_disagreement_C"] = [0.1 + 0.08 * i for i in range(n)]
    t["mae_C_idw"] = 0.5 + 1.0 * t["idw_disagreement_C"] + [(-0.03, 0.03)[i % 2] for i in range(n)]
    return t


def test_disagreement_model_recovers_the_line():
    p = source_select.predict_error(make_gap_table(), "disagreement", 30, 0, disagreement=1.0)
    assert abs(p["expected_mae_C"] - 1.5) < 0.05
    assert p["range_mae_C"][0] <= p["expected_mae_C"] <= p["range_mae_C"][1]


def test_disagreement_beats_table_average_when_it_carries_signal():
    ev = source_select.evaluate_error_models(make_gap_table())
    s = ev["scores"]
    assert s["disagreement"]["mean_miss_C"] < s["table_average"]["mean_miss_C"]
    assert ev["best_model"] in ("disagreement", "disagreement+geometry")
    assert ev["gain_vs_table_average_C"] > 0


def test_expected_error_falls_back_without_disagreement(monkeypatch, tmp_path):
    path = tmp_path / "loso.csv"
    make_gap_table().to_csv(path, index=False)
    monkeypatch.setattr(config, "VALIDATION_TABLE", path)
    source_select._error_evaluation_for.cache_clear()
    with_gap = source_select.expected_error_for("idw", 30, 0, disagreement=1.0)
    without = source_select.expected_error_for("idw", 30, 0, disagreement=None)  # Open-Meteo offline
    assert with_gap["model"].startswith("disagreement")
    assert without["model"] == "similar_stations"
    assert source_select.expected_error_for("nearest", 30, 0, 1.0) is None  # signal is about IDW only


def test_expected_error_warns_outside_tested_range(monkeypatch, tmp_path):
    path = tmp_path / "loso.csv"
    make_gap_table().to_csv(path, index=False)  # distances 10-69 km, disagreement 0.1-1.94 °C
    monkeypatch.setattr(config, "VALIDATION_TABLE", path)
    source_select._error_evaluation_for.cache_clear()
    inside = source_select.expected_error_for("idw", 30, 0, disagreement=1.0)
    close = source_select.expected_error_for("idw", 3, 0, disagreement=1.0)
    assert inside["within_tested_range"] is True and "warning" not in inside
    assert close["within_tested_range"] is False and "km" in close["warning"]
