"""Which temperature source can stand in for a station? Score them where we know the truth.

For one CIMIS station we pretend it doesn't exist, estimate its temperature three ways,
and compare each estimate with what the station really measured:
  nearest     the closest other station's reading, used as is
  idw         nearby stations weighted by distance, moved to this elevation (interp.py)
  open_meteo  the Open-Meteo historical model at this station's coordinates

All three are scored on exactly the same hours, so the comparison is fair.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import chill, config, interp, openmeteo_hist

METHODS = ("nearest", "idw", "open_meteo")
TOTALS = ("chill_hours", "utah_units", "chill_portions", "gdd")


def error_metrics(actual_f: pd.Series, predicted_f: pd.Series) -> dict:
    """Hourly error of predicted minus actual: MAE, RMSE, bias in °F and °C, share within ±WITHIN_C.

    MAE  = average size of the error (how far off, typically).
    RMSE = like MAE but big misses count more.
    Bias = average signed error (+ means the estimate runs warm).
    Hours where either side is missing are skipped.
    """
    both = pd.concat([actual_f.rename("a"), predicted_f.rename("p")], axis=1).dropna()
    if both.empty:
        return {"hours_compared": 0}
    err_f = both["p"] - both["a"]
    err_c = err_f * 5 / 9  # a temperature *difference* converts without the -32
    return {
        "hours_compared": int(len(both)),
        "mae_F": round(float(err_f.abs().mean()), 2),
        "rmse_F": round(float(np.sqrt((err_f ** 2).mean())), 2),
        "bias_F": round(float(err_f.mean()), 2),
        "mae_C": round(float(err_c.abs().mean()), 2),
        "rmse_C": round(float(np.sqrt((err_c ** 2).mean())), 2),
        "bias_C": round(float(err_c.mean()), 2),
        f"pct_within_{config.WITHIN_C:g}C": round(float((err_c.abs() <= config.WITHIN_C).mean() * 100), 1),
    }


def compare_methods(stations: pd.DataFrame, hourly: dict[str, pd.Series], station_id: str,
                    start, end, crop: str = "almond") -> dict:
    """Hide station_id and score nearest / idw / open_meteo against its real readings.

    hourly: station_id -> hourly °F series for the target and its neighbors (from data.py).
    Returns per-method hourly errors, season-total errors (chill, GDD), and the winner by MAE.
    """
    target = stations.set_index("station_id").loc[station_id]
    actual = hourly[station_id]
    neigh = interp.nearest(stations, target.lat, target.lon, config.NEIGHBORS, exclude=[station_id])

    # nearest: the closest neighbor that actually has data for this window
    with_data = neigh[neigh["station_id"].isin([s for s, v in hourly.items() if not v.dropna().empty])]
    nearest_row = with_data.iloc[0] if not with_data.empty else None

    estimates = {
        "nearest": hourly[nearest_row.station_id] if nearest_row is not None else pd.Series(dtype=float),
        "idw": interp.estimate_series(hourly, neigh, target.elev_m),
        "open_meteo": openmeteo_hist.hourly_archive(target.lat, target.lon, start, end, elev_m=target.elev_m),
    }

    # Same hours for everyone: keep only hours where the station and all three estimates exist.
    frame = pd.concat([actual.rename("actual")] + [estimates[m].rename(m) for m in METHODS], axis=1)
    available = [m for m in METHODS if frame[m].notna().any()]
    common = frame[["actual"] + available].dropna()

    truth = chill.summarize(common["actual"], crop)
    methods = {}
    for m in METHODS:
        if m not in available:
            methods[m] = {"available": False}
            continue
        est_totals = chill.summarize(common[m], crop)
        methods[m] = {
            "available": True,
            **error_metrics(common["actual"], common[m]),
            "season_total_error": {k: round(est_totals[k] - truth[k], 1) for k in TOTALS},
        }
    methods["nearest"]["station"] = None if nearest_row is None else {
        "station_id": nearest_row.station_id, "name": nearest_row.get("name"),
        "dist_km": round(float(nearest_row.dist_km), 1),
        "elev_diff_m": round(float(nearest_row.elev_m - target.elev_m), 1),
    }
    methods["open_meteo"]["model"] = config.OPENMETEO_MODEL

    scored = {m: methods[m]["mae_F"] for m in available if "mae_F" in methods[m]}
    best = min(scored, key=scored.get) if scored else None
    om_vs_nearest = (round(scored["nearest"] - scored["open_meteo"], 2)
                     if {"nearest", "open_meteo"} <= scored.keys() else None)
    return {
        "station_id": station_id,
        "window": {"start": str(start), "end": str(end)},
        "hours_in_station_record": int(actual.notna().sum()),
        "hours_compared": int(len(common)),
        "actual_totals": {k: round(truth[k], 1) for k in TOTALS},
        "methods": methods,
        "best_method_by_mae": best,
        # > 0: Open-Meteo is closer to the real station than the nearest other station is.
        "open_meteo_mae_gain_vs_nearest_F": om_vs_nearest,
        "note": "One station and one season: a pilot check, not a general accuracy claim.",
    }
