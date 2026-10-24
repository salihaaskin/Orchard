"""How well can we fill a broken sensor? Hide real CIMIS readings and compare fills.

For every station-season in the validation table we hide stretches of 3, 6, 12 and 24
hours (random places where the real readings exist), fill them every way sensor_health
knows, and compare with what the station really measured:
  - hourly temperature error (MAE, °C)
  - chill hours error inside the hidden stretch (hours with 32 < T <= 45 °F)
Writes data/validation/fill_experiment.csv and prints a summary.

    cd backend && python -m scripts.sensor_fill_experiment
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app import config, data, interp, seasons, sensor_health, source_compare, source_select
from app.chill import chill_hours
from app.main import load_hourly

GAP_HOURS = (3, 6, 12, 24)
GAPS_PER_LENGTH = 20  # per station-season
SEED = 42


def chill_hours_in(temps_f: pd.Series) -> float:
    """Number of chill hours (32 < T <= 45 °F) in a stretch."""
    return float(chill_hours(temps_f)[-1]) if len(temps_f) else 0.0


def run():
    """Hide, fill, score; one row per station-season x gap length x fill method."""
    table = source_select.load_table()
    stations = data.get_stations()
    by_id = stations.set_index("station_id")
    rng = np.random.default_rng(SEED)
    rows = []
    for _, case in table.iterrows():
        sid = case["station_id"]
        start, end, _ = seasons.chill_window(date.fromisoformat(case["season_end"]))
        t = by_id.loc[sid]
        pool = interp.nearest(stations, t.lat, t.lon, 3 * config.NEIGHBORS, exclude=[sid])["station_id"]
        hourly = load_hourly(list(pool) + [sid], start, end)
        actual = hourly[sid]
        estimates, *_ = source_compare.build_estimates(stations, hourly, sid, start, end)
        idx = actual.index
        for length in GAP_HOURS:
            errs = {}
            for _ in range(GAPS_PER_LENGTH):
                # a stretch where the real readings (and one hour either side) all exist
                for _try in range(50):
                    i = int(rng.integers(1, len(idx) - length - 1))
                    window = actual.iloc[i - 1:i + length + 1]
                    if window.notna().all() and (window.index[-1] - window.index[0]) == pd.Timedelta(hours=length + 1):
                        break
                else:
                    continue
                gap = actual.index[i:i + length]
                truth = actual.reindex(gap)
                hidden = actual.copy()
                hidden.loc[gap] = np.nan
                for name, fill in sensor_health.fill_options(hidden, estimates, gap).items():
                    if fill.isna().any():
                        continue
                    e = errs.setdefault(name, {"mae": [], "ch": []})
                    e["mae"].append(float((fill - truth).abs().mean() * 5 / 9))
                    e["ch"].append(abs(chill_hours_in(fill) - chill_hours_in(truth)))
            for name, e in errs.items():
                rows.append({"station_id": sid, "season_end": case["season_end"],
                             "nearest_elev_diff_m": case["nearest_elev_diff_m"],
                             "gap_hours": length, "fill": name, "n_gaps": len(e["mae"]),
                             "mae_C": np.mean(e["mae"]), "chill_hours_abs_error": np.mean(e["ch"])})
        print(f"{sid} {case['season_end']} done")
    out = pd.DataFrame(rows)
    path = config.VALIDATION_TABLE.parent / "fill_experiment.csv"
    out.to_csv(path, index=False)
    print(f"wrote {len(out)} rows to {path}")
    summary = out.pivot_table(index="fill", columns="gap_hours", values="mae_C", aggfunc="mean").round(2)
    print("\nMean hourly error (°C) by fill and gap length:\n", summary.sort_values(24).to_string())
    ch = out.pivot_table(index="fill", columns="gap_hours", values="chill_hours_abs_error", aggfunc="mean").round(2)
    print("\nMean chill-hours error inside the gap:\n", ch.sort_values(24).to_string())


if __name__ == "__main__":
    run()
