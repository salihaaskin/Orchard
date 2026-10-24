"""Does sensor_health.detect_faults catch broken sensors without crying wolf?

For every station-season in the validation table, with IDW from neighbors as the reference:
  1. Alarms on the real record. We have no CIMIS quality flags, so some may be real faults;
     few alarms here means few false alarms.
  2. Plant one fault of each kind at a random place and check it is caught:
     stuck 8 h, drift +6 °C for 24 h, one-hour spike +20 °F, dropout 8 h.
Also prints how far healthy stations stray from the reference (to check the drift threshold).

    cd backend && python -m scripts.sensor_detection_experiment
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app import config, data, interp, seasons, sensor_health, source_compare, source_select
from app.main import load_hourly

SEED = 7


def plant(sensor: pd.Series, kind: str, rng) -> tuple[pd.Series, pd.DatetimeIndex]:
    """Copy of the record with one fault planted where the readings exist; returns its hours."""
    length = {"stuck": 8, "drift": 24, "spike": 1, "missing": 8}[kind]
    idx = sensor.index
    for _ in range(100):
        i = int(rng.integers(24 * 8, len(idx) - length - 2))  # after the first week (drift baseline)
        hours = idx[i:i + length]
        if sensor.reindex(hours).notna().all() and (hours[-1] - hours[0]) == pd.Timedelta(hours=length - 1):
            break
    return sensor_health.plant_fault(sensor, kind, hours[0], length)


def run():
    """Score alarms on real records and catches on planted faults."""
    table = source_select.load_table()
    stations = data.get_stations()
    by_id = stations.set_index("station_id")
    rng = np.random.default_rng(SEED)
    alarm_rows, catch_rows, peaks = [], [], []
    for _, case in table.iterrows():
        sid = case["station_id"]
        start, end, _ = seasons.chill_window(date.fromisoformat(case["season_end"]))
        t = by_id.loc[sid]
        pool = interp.nearest(stations, t.lat, t.lon, 3 * config.NEIGHBORS, exclude=[sid])["station_id"]
        hourly = load_hourly(list(pool) + [sid], start, end)
        raw = data.get_hourly(sid, start, end).set_index("datetime")
        sensor, filled = raw["temp_F"], raw["is_filled"]
        ref = source_compare.build_estimates(stations, hourly, sid, start, end)[0]["idw"]

        found = sensor_health.detect_faults(sensor, ref, filled)
        kinds = pd.Series([e["type"] for e in found["episodes"]]).value_counts().to_dict()
        alarm_rows.append({"station_id": sid, "season_end": case["season_end"],
                           "faulty_hours": int(found["faulty"].sum()), "episodes": len(found["episodes"]),
                           **kinds, "flat_notes": len(found["notes"])})
        gap_c = ((sensor - ref).abs() * 5 / 9).rolling(config.SENSOR_DEVIATION_HOURS).mean()
        peaks.append(float(gap_c.max()))

        for kind in ("stuck", "drift", "spike", "missing"):
            broken, hours = plant(sensor, kind, rng)
            res = sensor_health.detect_faults(broken, ref, filled)
            caught = bool(res["faulty"].reindex(hours).fillna(False).any())
            right_type = any(e["type"] == kind and pd.Timestamp(e["start"]) <= hours[-1]
                             and pd.Timestamp(e["end"]) >= hours[0] for e in res["episodes"])
            harm_c = (float(((broken - sensor).abs().reindex(hours) * 5 / 9).mean())
                      if kind != "missing" else np.nan)
            catch_rows.append({"station_id": sid, "season_end": case["season_end"], "fault": kind,
                               "caught": caught, "right_type": right_type, "fault_error_C": harm_c})
        print(f"{sid} {case['season_end']}: {len(found['episodes'])} alarms on the real record")

    alarms = pd.DataFrame(alarm_rows).fillna(0)
    catches = pd.DataFrame(catch_rows)
    path = config.VALIDATION_TABLE.parent
    alarms.to_csv(path / "detection_alarms.csv", index=False)
    catches.to_csv(path / "detection_catches.csv", index=False)
    print(f"\nReal records: {len(alarms)} station-seasons, {int(alarms['episodes'].sum())} alarms, "
          f"{int(alarms['faulty_hours'].sum())} hours flagged of {len(alarms) * 2880}")
    print(alarms.drop(columns=["station_id", "season_end"]).sum().to_string())
    print("\nPlanted faults caught (% of station-seasons):")
    print((catches.groupby("fault")[["caught", "right_type"]].mean() * 100).round(1).to_string())
    print()
    print("How wrong the planted stuck readings were (°C), caught vs missed:")
    print(catches[catches["fault"] == "stuck"].groupby("caught")["fault_error_C"].describe().round(2).to_string())
    print(f"\nHealthy max {config.SENSOR_DEVIATION_HOURS}-h mean gap to reference (°C, raw): "
          f"median {np.median(peaks):.2f}, 95th pct {np.percentile(peaks, 95):.2f}, max {max(peaks):.2f}")


if __name__ == "__main__":
    run()
