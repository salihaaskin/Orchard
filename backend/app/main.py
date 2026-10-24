"""Chill at My Orchard API.

Answers the grower's three questions for any point:
  1. How much chill / GDD so far?          -> /estimate  "chill", "gdd"
  2. How does that compare to past years?   -> /estimate  "history"
  3. What should I expect in 1-2 weeks?     -> /estimate  "projection"
And proves it works:                        -> /validate  (leave one station out)
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from . import chill, config, data, forecast, interp, seasons, source_compare

app = FastAPI(title="Chill at My Orchard", version="0.1.0")
METRICS = ("chill_portions", "chill_hours", "hours_lt_45", "utah_units")


# ---------------------------------------------------------------- helpers

def load_hourly(station_ids, start: date, end: date) -> dict[str, pd.Series]:
    out = {}
    for sid in station_ids:
        try:
            df = data.get_hourly(sid, start, end)
        except Exception:
            continue  # station offline for this window: the estimate just uses the others
        if not df.empty:
            out[sid] = df.set_index("datetime")["temp_F"]
    return out


def clip(s: pd.Series, start: date, end: date) -> pd.Series:
    return s[(s.index >= pd.Timestamp(start)) & (s.index < pd.Timestamp(end) + pd.Timedelta(days=1))]


def daily_curve(s: pd.Series) -> list[float]:
    """Cumulative chill portions at the end of each day of the season (for the chart)."""
    if s.empty:
        return []
    cp = pd.Series(chill.chill_portions(s), index=s.index)
    return [round(float(v), 2) for v in cp.resample("D").last().ffill()]


def estimate_at(lat, lon, elev, start, end, exclude) -> tuple[pd.Series, pd.DataFrame]:
    stations = data.get_stations()
    neigh = interp.nearest(stations, lat, lon, config.NEIGHBORS, exclude=exclude)
    hourly = load_hourly(neigh["station_id"], start, end)
    return interp.estimate_series(hourly, neigh, elev), neigh


# ---------------------------------------------------------------- endpoints

@app.get("/health")
def health():
    return {"ok": True, "data_source": config.DATA_SOURCE}


@app.get("/stations")
def stations():
    return data.get_stations().to_dict("records")


@app.get("/demo")
def demo():
    return config.DEMO_ORCHARDS


@app.get("/estimate")
def estimate(
    lat: float, lon: float,
    as_of: date = Query(default_factory=date.today),
    crop: str = "almond",
    exclude: list[str] = Query(default=[]),
    history_seasons: int = 10,
    range_low_f: float = config.CUSTOM_RANGE_F[0],
    range_high_f: float = config.CUSTOM_RANGE_F[1],
):
    elev = interp.elevation_m(lat, lon)
    elev_source = "open-meteo"
    stations_df = data.get_stations()
    if elev is None:
        elev = float(interp.nearest(stations_df, lat, lon, 1)["elev_m"].iloc[0])
        elev_source = "nearest station (offline)"

    # 1. So far
    c_start, c_end, complete = seasons.chill_window(as_of)
    est, neigh = estimate_at(lat, lon, elev, c_start, c_end, exclude)
    if est.dropna().empty:
        raise HTTPException(404, "No station data near this point for this season")
    est = clip(est, c_start, c_end)
    so_far = chill.summarize(est, crop, custom_range=(range_low_f, range_high_f))

    g_start, g_end, g_started = seasons.gdd_window(as_of)
    if g_started:
        g_est, _ = estimate_at(lat, lon, elev, g_start, g_end, exclude)
        gdd = chill.summarize(clip(g_est, g_start, g_end), crop)
    else:
        gdd = chill.summarize(pd.Series(dtype=float, index=pd.DatetimeIndex([])), crop)

    # 2. Past years: same window, shifted back; extend 14 days for the climatology projection
    history = []
    for k in range(1, history_seasons + 1):
        h_start, h_end = seasons.same_window_in(k, c_start, c_end)
        h_est, _ = estimate_at(lat, lon, elev, h_start, seasons.plus_days(h_end, 14), exclude)
        if h_est.dropna().empty:
            continue
        at_date = chill.summarize(clip(h_est, h_start, h_end), crop)
        plus14 = chill.summarize(clip(h_est, h_start, seasons.plus_days(h_end, 14)), crop)
        history.append({
            "season": f"{h_start.year}-{str(h_start.year + 1)[-2:]}",
            "at_date": {m: round(at_date[m], 1) for m in METRICS},
            "next_14_days": {m: round(plus14[m] - at_date[m], 1) for m in METRICS},
            "curve": daily_curve(clip(h_est, h_start, seasons.plus_days(h_end, 14))),
        })
    pct = {}
    if history:
        for m in METRICS:
            vals = [h["at_date"][m] for h in history]
            pct[m] = {p: round(float(np.percentile(vals, q)), 1)
                      for p, q in (("p10", 10), ("p50", 50), ("p90", 90))}

    # 3. Next 14 days: live forecast if we're in the present, else the historical median
    projection = {"days": 14}
    live = as_of >= date.today() - timedelta(days=1) and not complete
    if live:
        fc, src = forecast.hourly_forecast(lat, lon)
        fc = fc[fc.index > est.index.max()]
        if not fc.empty:
            combined = pd.concat([est, fc])
            after = chill.summarize(combined, crop)
            projection.update(method="forecast", source=src,
                              added={m: round(after[m] - so_far[m], 1) for m in METRICS})
    if "added" not in projection and history:
        projection.update(method="climatology (median of past seasons)", source="history",
                          added={m: round(float(np.median([h["next_14_days"][m] for h in history])), 1)
                                 for m in METRICS})
    if "added" in projection:
        projection["projected_total"] = {m: round(so_far[m] + projection["added"][m], 1) for m in METRICS}

    # In replay mode we can also show what actually happened next (honest check of the projection)
    if not live and not complete:
        nxt, _ = estimate_at(lat, lon, elev, c_start, seasons.plus_days(c_end, 14), exclude)
        actual_after = chill.summarize(clip(nxt, c_start, seasons.plus_days(c_end, 14)), crop)
        projection["actually_added"] = {m: round(actual_after[m] - so_far[m], 1) for m in METRICS}

    return {
        "location": {"lat": lat, "lon": lon, "elev_m": round(elev, 1), "elev_source": elev_source},
        "as_of": str(as_of), "crop": crop, "data_source": config.DATA_SOURCE,
        "chill": {"start": str(c_start), "end": str(c_end), "season_complete": complete,
                  **{m: round(so_far[m], 1) for m in METRICS},
                  "hours_counted": so_far["hours_counted"], "hours_missing": so_far["hours_missing"],
                  "custom_range": so_far["custom_range"],
                  "curve": daily_curve(est)},
        "gdd": {"start": str(g_start), "end": str(g_end), "started": g_started, "total_c_days": round(gdd["gdd"], 1),
                "method": "single sine", "thresholds_c": gdd["gdd_thresholds_c"]},
        "history": {"seasons": history, "percentiles": pct},
        "projection": projection,
        "neighbors": neigh[["station_id", "name", "dist_km", "elev_m"]].round(1).to_dict("records"),
        "excluded": exclude,
    }


@app.get("/validate")
def validate(station_id: str, as_of: date = Query(default=date(2025, 2, 28))):
    """Hide a station; estimate it from neighbors with and without the elevation adjustment.

    method_comparison also scores nearest station, IDW, Open-Meteo and corrected Open-Meteo
    on the same hours.
    """
    stations_df = data.get_stations()
    if station_id not in set(stations_df["station_id"]):
        raise HTTPException(404, f"Unknown station {station_id}")
    c_start, c_end, _ = seasons.chill_window(as_of)
    target = stations_df.set_index("station_id").loc[station_id]
    neigh = interp.nearest(stations_df, target.lat, target.lon, config.NEIGHBORS, exclude=[station_id])
    hourly = load_hourly(list(neigh["station_id"]) + [station_id], c_start, c_end)
    if station_id not in hourly:
        raise HTTPException(404, f"No data for station {station_id} in {c_start}..{c_end}")
    summarize = lambda s: chill.summarize(s)  # noqa: E731
    with_elev = interp.leave_one_out(stations_df, hourly, station_id, summarize)
    no_elev = interp.leave_one_out(stations_df, hourly, station_id, summarize, lapse_f_per_m=0.0)
    comparison = source_compare.compare_methods(stations_df, hourly, station_id, c_start, c_end)
    return {"season": f"{c_start} to {c_end}", "with_elevation_adjustment": with_elev,
            "no_elevation_adjustment": no_elev, "method_comparison": comparison,
            "data_source": config.DATA_SOURCE,
            "generated": datetime.now().isoformat(timespec="seconds")}


@app.get("/validate/all")
def validate_all(
    as_of: date = Query(default=date(2025, 2, 28)),
    lat: float = config.DEMO_ORCHARDS["durham"]["lat"],
    lon: float = config.DEMO_ORCHARDS["durham"]["lon"],
    radius_km: float = 150.0,
    max_stations: int = 12,
    include_inactive: bool = False,
):
    """Run method_comparison for each station near (lat, lon), one at a time.

    Gives the evidence table: per station, how well nearest station / IDW / Open-Meteo
    stand in for it, plus averages grouped by distance to the nearest neighbor.
    First run fetches from CIMIS and Open-Meteo (a few minutes); later runs use the cache.
    """
    stations_df = data.get_stations()
    c_start, c_end, _ = seasons.chill_window(as_of)
    pool = stations_df if include_inactive else stations_df[stations_df["active"]]
    targets = interp.nearest(pool, lat, lon, max_stations)
    targets = targets[targets["dist_km"] <= radius_km]

    rows, skipped = [], []
    for sid in targets["station_id"]:
        target = stations_df.set_index("station_id").loc[sid]
        # Load a wider pool than NEIGHBORS: some nearby stations have closed and return no data.
        pool_ids = interp.nearest(stations_df, target.lat, target.lon, 3 * config.NEIGHBORS,
                                  exclude=[sid])["station_id"]
        hourly = load_hourly(list(pool_ids) + [sid], c_start, c_end)
        if sid not in hourly or hourly[sid].dropna().empty:
            skipped.append({"station_id": sid, "reason": "no station data this season"})
            continue
        result = source_compare.compare_methods(stations_df, hourly, sid, c_start, c_end)
        if result["hours_compared"] < source_compare.MIN_HOURS:
            skipped.append({"station_id": sid, "reason": f"only {result['hours_compared']} shared hours"})
            continue
        rows.append(source_compare.station_row(result))

    return {
        "season": f"{c_start} to {c_end}",
        "center": {"lat": lat, "lon": lon, "radius_km": radius_km},
        "open_meteo_model": config.OPENMETEO_MODEL,
        "stations": rows,
        "by_nearest_distance": source_compare.summarize_by_distance(rows),
        **{f"{m}_beats_nearest": sum(1 for r in rows if (r[f"{m}_mae_gain_vs_nearest_C"] or 0) > 0)
           for m in source_compare.OPEN_METEO_METHODS},
        "stations_compared": len(rows),
        "skipped": skipped,
        "data_source": config.DATA_SOURCE,
        "note": "Pilot check over one season; few stations per distance group means weak evidence.",
    }
