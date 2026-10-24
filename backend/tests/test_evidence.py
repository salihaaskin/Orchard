"""Checks for the /evidence summary behind the "How we know" page."""
import os

os.environ["DATA_SOURCE"] = "synthetic"

from fastapi.testclient import TestClient  # noqa: E402

from app import config, evidence  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def test_evidence_endpoint_shape():
    r = client.get("/evidence")
    assert r.status_code == 200, r.text
    body = r.json()
    if not body["available"]:  # needs data/validation/loso_table.csv
        return
    assert body["summary"]["cases"] == len(body["stations"])
    assert set(body["method_errors"]["Chill portions"]) == {"nearest", "idw", "open_meteo", "open_meteo_corrected"}
    assert {"chill_portions", "hourly_temp"} <= set(body["elevation_rule"])
    assert sum(r["cases"] for r in body["by_elevation"]) == body["summary"]["cases"]


def test_evidence_without_table(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VALIDATION_TABLE", tmp_path / "missing.csv")
    assert evidence.evidence() == {"available": False}


def test_clean_makes_json_safe():
    import numpy as np
    out = evidence._clean({"a": np.float64(1.23456), "b": float("nan"), "c": np.int64(3), "d": [np.bool_(True)]})
    assert out == {"a": 1.235, "b": None, "c": 3, "d": [True]}
