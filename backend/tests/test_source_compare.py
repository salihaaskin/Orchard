"""Checks for the nearest / IDW / Open-Meteo comparison (synthetic data, no internet)."""
import os

os.environ["DATA_SOURCE"] = "synthetic"

from datetime import date  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import openmeteo_hist, source_compare  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_error_metrics_by_hand():
    idx = pd.date_range("2025-01-01", periods=4, freq="h")
    actual = pd.Series([40.0, 42.0, 44.0, np.nan], index=idx)
    predicted = pd.Series([41.0, 40.0, 44.0, 50.0], index=idx)  # errors +1, -2, 0; last hour skipped
    m = source_compare.error_metrics(actual, predicted)
    assert m["hours_compared"] == 3
    assert m["mae_F"] == 1.0
    assert m["bias_F"] == round(-1 / 3, 2)
    assert m["rmse_F"] == round(np.sqrt(5 / 3), 2)
    assert m["pct_within_2C"] == 100.0  # 2 °F = 1.1 °C, all inside ±2 °C


def test_error_metrics_no_overlap():
    a = pd.Series([40.0], index=pd.to_datetime(["2025-01-01 00:00"]))
    p = pd.Series([40.0], index=pd.to_datetime(["2025-01-02 00:00"]))
    assert source_compare.error_metrics(a, p) == {"hours_compared": 0}


def test_archive_covers_whole_window():
    s = openmeteo_hist.hourly_archive(39.6, -121.8, date(2025, 1, 1), date(2025, 1, 2))
    assert len(s) == 48
    assert s.index[0] == pd.Timestamp("2025-01-01 00:00")
    assert s.index[-1] == pd.Timestamp("2025-01-02 23:00")


def test_validate_scores_three_methods_on_same_hours():
    r = client.get("/validate", params={"station_id": "SYN-B", "as_of": "2025-02-28"})
    assert r.status_code == 200, r.text
    c = r.json()["method_comparison"]
    assert c["hours_compared"] > 1000
    for m in ("nearest", "idw", "open_meteo"):
        assert c["methods"][m]["available"] is True
        assert c["methods"][m]["hours_compared"] == c["hours_compared"]
        assert "chill_portions" in c["methods"][m]["season_total_error"]
    assert c["methods"]["nearest"]["station"]["station_id"] != "SYN-B"
    assert c["best_method_by_mae"] in ("nearest", "idw", "open_meteo")
    # the synthetic model is built 1 °F warm; validation should see that bias
    assert 0.5 < c["methods"]["open_meteo"]["bias_F"] < 1.5
