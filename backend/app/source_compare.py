"""Which temperature source can stand in for a station? Score them where we know the truth.

For one CIMIS station we pretend it doesn't exist, estimate its temperature four ways,
and compare each estimate with what the station really measured:
  nearest               the closest other station's reading, used as is
  idw                   nearby stations weighted by distance, moved to this elevation (interp.py)
  open_meteo            the Open-Meteo historical model at this station's coordinates
  open_meteo_corrected  the nearest station's reading, shifted by the difference Open-Meteo
                        sees between the two places that hour (model error shared by both
                        places cancels out)

All four are scored on exactly the same hours, so the comparison is fair.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import chill, config, interp, openmeteo_hist

METHODS = ("nearest", "idw", "open_meteo", "open_meteo_corrected")
OPEN_METEO_METHODS = ("open_meteo", "open_meteo_corrected")
TOTALS = ("chill_hours", "utah_units", "chill_portions", "gdd")

# A station needs at least 30 days of shared hours to count in the multi-station summary.
MIN_HOURS = 30 * 24
# Group stations by how far away their nearest neighbor is (km): does Open-Meteo win when it's far?
DISTANCE_BINS_KM = (0, 15, 30, 50, float("inf"))


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
    """Hide station_id and score each of METHODS against its real readings.

    hourly: station_id -> hourly °F series for the target and its neighbors (from data.py).
    Returns per-method hourly errors, season-total errors (chill, GDD), and the winner by MAE.
    """
    target = stations.set_index("station_id").loc[station_id]
    actual = hourly[station_id]
    # Neighbors come only from stations that actually have data this window: closed stations
    # still sit in the CIMIS list and would otherwise leave nearest/IDW with nothing to use.
    has_data = [s for s, v in hourly.items() if not v.dropna().empty]
    neigh = interp.nearest(stations[stations["station_id"].isin(has_data)], target.lat, target.lon,
                           config.NEIGHBORS, exclude=[station_id])
    nearest_row = neigh.iloc[0] if not neigh.empty else None

    empty = pd.Series(dtype=float)
    om_target = openmeteo_hist.hourly_archive(target.lat, target.lon, start, end, elev_m=target.elev_m)
    estimates = {
        "nearest": hourly[nearest_row.station_id] if nearest_row is not None else empty,
        "idw": interp.estimate_series(hourly, neigh, target.elev_m),
        "open_meteo": om_target,
        "open_meteo_corrected": empty,
    }
    if nearest_row is not None and not om_target.empty:
        om_near = openmeteo_hist.hourly_archive(nearest_row.lat, nearest_row.lon, start, end,
                                                elev_m=nearest_row.elev_m)
        # hour by hour: real reading over there + (model here - model over there)
        estimates["open_meteo_corrected"] = estimates["nearest"] + (om_target - om_near)

    # Same hours for everyone: keep only hours where the station and all estimates exist.
    # An empty estimate (e.g. Open-Meteo unreachable) is left out: it has no dates to line up on.
    present = [m for m in METHODS if not estimates[m].dropna().empty]
    frame = pd.concat([actual.rename("actual")] + [estimates[m].rename(m) for m in present], axis=1)
    available = present
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
    for m in OPEN_METEO_METHODS:
        methods[m]["model"] = config.OPENMETEO_MODEL

    scored = {m: methods[m]["mae_F"] for m in available if "mae_F" in methods[m]}
    best = min(scored, key=scored.get) if scored else None
    # > 0: this Open-Meteo method is closer to the real station than the nearest other station is.
    gains = {m: (round(scored["nearest"] - scored[m], 2) if {"nearest", m} <= scored.keys() else None)
             for m in OPEN_METEO_METHODS}
    return {
        "station_id": station_id,
        "name": target.get("name"),
        "elev_m": round(float(target.elev_m), 1),
        "window": {"start": str(start), "end": str(end)},
        "hours_in_station_record": int(actual.notna().sum()),
        "hours_compared": int(len(common)),
        "actual_totals": {k: round(truth[k], 1) for k in TOTALS},
        "methods": methods,
        "best_method_by_mae": best,
        **{f"{m}_mae_gain_vs_nearest_F": gains[m] for m in OPEN_METEO_METHODS},
        "note": "One station and one season: a pilot check, not a general accuracy claim.",
    }


def station_row(result: dict) -> dict:
    """Flatten one compare_methods() result into a single table row (°C, for charts)."""
    m = result["methods"]
    near = m["nearest"].get("station") or {}
    row = {
        "station_id": result["station_id"], "name": result["name"], "elev_m": result["elev_m"],
        "hours_compared": result["hours_compared"],
        "nearest_station_id": near.get("station_id"),
        "nearest_dist_km": near.get("dist_km"),
        "nearest_elev_diff_m": near.get("elev_diff_m"),
        "best_method_by_mae": result["best_method_by_mae"],
    }
    within = f"pct_within_{config.WITHIN_C:g}C"
    for name in METHODS:
        row[f"mae_C_{name}"] = m[name].get("mae_C")
        row[f"bias_C_{name}"] = m[name].get("bias_C")
        row[f"{within}_{name}"] = m[name].get(within)
        row[f"chill_portions_error_{name}"] = m[name].get("season_total_error", {}).get("chill_portions")
    for name in OPEN_METEO_METHODS:
        # > 0: this Open-Meteo method is closer to the real station than the nearest other station is.
        near_mae, om_mae = row["mae_C_nearest"], row[f"mae_C_{name}"]
        row[f"{name}_mae_gain_vs_nearest_C"] = (round(near_mae - om_mae, 2)
                                                if near_mae is not None and om_mae is not None else None)
    return row


def summarize_by_distance(rows: list[dict]) -> list[dict]:
    """Average MAE per method for stations grouped by distance to their nearest neighbor.

    This answers "past what distance should we trust Open-Meteo over the nearest station?"
    Groups with no stations are left out. Few stations per group = weak evidence; n is reported.
    """
    out = []
    edges = DISTANCE_BINS_KM
    for lo, hi in zip(edges[:-1], edges[1:]):
        group = [r for r in rows if r["nearest_dist_km"] is not None and lo <= r["nearest_dist_km"] < hi]
        if not group:
            continue
        entry = {"dist_km": f"{lo:g}-{hi:g}" if hi != float("inf") else f"{lo:g}+", "n_stations": len(group)}
        for name in METHODS:
            vals = [r[f"mae_C_{name}"] for r in group if r[f"mae_C_{name}"] is not None]
            entry[f"mean_mae_C_{name}"] = round(float(np.mean(vals)), 2) if vals else None
        for name in OPEN_METEO_METHODS:
            entry[f"{name}_beats_nearest"] = sum(
                1 for r in group if (r[f"{name}_mae_gain_vs_nearest_C"] or 0) > 0)
        out.append(entry)
    return out
