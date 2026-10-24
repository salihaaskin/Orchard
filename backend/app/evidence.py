"""Everything we measured, summarized for the "How we know" page (GET /evidence).

Nothing new is computed here: this reads the leave-one-station-out table, the sensor
experiments in data/validation/, and the checks source_select already runs, and shapes
them into small tables and numbers a chart can use.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import config, data, source_select
from .source_compare import METHODS

ELEV_BINS_M = (0, 25, 50, 100, 10_000)
ELEV_LABELS = ("0-25 m", "25-50 m", "50-100 m", "100+ m")
TOTALS = {"mae_C": "Hourly temperature (°C)", "chill_portions_error": "Chill portions",
          "chill_hours_error": "Chill hours", "utah_units_error": "Utah units"}


def _clean(obj):
    """Make numpy numbers and NaN JSON-friendly."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else round(float(obj), 3)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _mean_abs(df: pd.DataFrame, prefix: str) -> dict:
    """Average size of the error for each method (column prefix_method)."""
    return {m: df[f"{prefix}_{m}"].abs().mean() for m in METHODS}


def stations_tested(table: pd.DataFrame) -> list[dict]:
    """One row per station-season with location, for the map and the table."""
    st = data.get_stations().set_index("station_id")[["lat", "lon"]]
    t = table.join(st, on="station_id")
    cols = ["station_id", "name", "region", "season_end", "lat", "lon", "elev_m", "nearest_dist_km",
            "nearest_elev_diff_m", "best_method_by_mae", "idw_disagreement_C"]
    cols += [f"mae_C_{m}" for m in METHODS] + [f"chill_portions_error_{m}" for m in METHODS]
    return t[cols].to_dict("records")


def by_elevation(table: pd.DataFrame) -> list[dict]:
    """Average chill-portion and temperature error per method, grouped by elevation difference."""
    t = table.assign(elev_bin=pd.cut(table["nearest_elev_diff_m"].abs(), ELEV_BINS_M,
                                     labels=ELEV_LABELS, include_lowest=True))
    rows = []
    for label, g in t.groupby("elev_bin", observed=True):
        rows.append({"elevation_difference": str(label), "cases": len(g),
                     "stations": int(g["station_id"].nunique()),
                     "chill_portions": _mean_abs(g, "chill_portions_error"),
                     "hourly_temp_C": _mean_abs(g, "mae_C")})
    return rows


def sensor_results() -> dict:
    """Fill experiment and detection experiment, summarized."""
    folder = config.VALIDATION_TABLE.parent
    out = {}
    fill_path = folder / "fill_experiment.csv"
    if fill_path.exists():
        f = pd.read_csv(fill_path)
        out["fill_mae_C"] = f.pivot_table(index="fill", columns="gap_hours", values="mae_C",
                                          aggfunc="mean").to_dict("index")
        out["fill_chill_hours_error"] = f.pivot_table(index="fill", columns="gap_hours",
                                                      values="chill_hours_abs_error",
                                                      aggfunc="mean").to_dict("index")
        # every gap was filled by each method, so count the gaps of one method
        out["fill_gaps_tested"] = int(f.loc[f["fill"] == "linear", "n_gaps"].sum())
    catches_path, alarms_path = folder / "detection_catches.csv", folder / "detection_alarms.csv"
    if catches_path.exists():
        c = pd.read_csv(catches_path)
        out["caught_pct"] = (c.groupby("fault")["caught"].mean() * 100).to_dict()
        stuck = c[c["fault"] == "stuck"]
        out["stuck_error_C"] = {"caught": stuck[stuck["caught"]]["fault_error_C"].mean(),
                                "missed": stuck[~stuck["caught"]]["fault_error_C"].mean()}
    if alarms_path.exists():
        a = pd.read_csv(alarms_path)
        out["alarms_per_station_season"] = a["episodes"].sum() / len(a)
        out["flagged_hours_pct"] = a["faulty_hours"].sum() / (len(a) * source_select.TABLE_WINDOW_HOURS) * 100
        out["station_seasons_checked"] = len(a)
    return out


@lru_cache(maxsize=2)
def _evidence_for(path: str, mtime: float) -> dict:
    table = source_select.load_table()
    if table.empty:
        return {"available": False}
    mt = config.VALIDATION_TABLE.stat().st_mtime
    rules = {q: source_select._elevation_evaluation_for(path, mt, q) for q in source_select.QUANTITIES}
    error_models = {q: source_select._error_evaluation_for(path, mt, q, "idw") for q in source_select.QUANTITIES}
    selector = source_select._evaluation_for(path, mt)
    return _clean({
        "available": True,
        "summary": {"cases": len(table), "stations": int(table["station_id"].nunique()),
                    "seasons": sorted(table["season_end"].unique().tolist()),
                    "regions": sorted(table["region"].dropna().unique().tolist()),
                    "open_meteo_model": config.OPENMETEO_MODEL},
        "stations": stations_tested(table),
        "method_errors": {label: _mean_abs(table, prefix) for prefix, label in TOTALS.items()},
        "wins_hourly": table["best_method_by_mae"].value_counts().to_dict(),
        "by_elevation": by_elevation(table),
        "elevation_rule": rules,
        "selector_by_distance": {k: selector[k] for k in ("strategies", "best_fixed_method",
                                                          "selector_gain_vs_best_fixed_C", "cases")},
        "error_models": {q: {k: ev.get(k) for k in ("scores", "best_model", "gain_vs_table_average",
                                                    "unit", "cases")} for q, ev in error_models.items()},
        "sensor": sensor_results(),
    })


def evidence() -> dict:
    """All results for the presentation page, recomputed only when the validation table changes."""
    if not config.VALIDATION_TABLE.exists():
        return {"available": False}
    return _evidence_for(str(config.VALIDATION_TABLE), config.VALIDATION_TABLE.stat().st_mtime)
