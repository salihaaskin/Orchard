"""Build the leave-one-station-out table the source selector learns from.

Runs /validate/all for several regions and seasons and saves one row per
station-season to config.VALIDATION_TABLE (data/validation/loso_table.csv).
First run downloads CIMIS and Open-Meteo data (10+ minutes); reruns use the cache.

    cd backend && python -m scripts.build_validation_table
"""
from __future__ import annotations

import pandas as pd
from fastapi.testclient import TestClient

from app import config
from app.main import app

# Region centers (lat, lon). Mix of valley floor and stations with big elevation differences.
REGIONS = {
    "sacramento_valley": (39.61, -121.82),
    "san_joaquin_valley": (36.60, -119.50),
    "central_coast": (36.67, -121.65),
}
# Chill seasons, named by their last day (Nov 1 of the year before .. this date).
SEASON_ENDS = ["2024-02-28", "2025-02-28"]
STATIONS_PER_REGION = 12
RADIUS_KM = 150


def main():
    """Run every region x season and write the combined table."""
    client = TestClient(app)
    frames = []
    for as_of in SEASON_ENDS:
        for region, (lat, lon) in REGIONS.items():
            r = client.get("/validate/all", params={"as_of": as_of, "lat": lat, "lon": lon,
                                                    "max_stations": STATIONS_PER_REGION,
                                                    "radius_km": RADIUS_KM})
            r.raise_for_status()
            body = r.json()
            df = pd.DataFrame(body["stations"])
            df["region"], df["season_end"] = region, as_of
            frames.append(df)
            print(f"{as_of} {region}: {len(df)} stations, skipped {len(body['skipped'])}")
    table = pd.concat(frames, ignore_index=True)
    # A station near two region centers appears twice in a season: keep one.
    table = table.drop_duplicates(subset=["station_id", "season_end"])
    config.VALIDATION_TABLE.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(config.VALIDATION_TABLE, index=False)
    print(f"wrote {len(table)} rows to {config.VALIDATION_TABLE}")


if __name__ == "__main__":
    main()
