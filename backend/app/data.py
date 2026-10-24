"""Station data: CIMIS (real) or synthetic (for building before real data lands).

Every function returns the team's data contract:
    station_id, lat, lon, elev_m, datetime, temp_F, source, is_filled
"""
from __future__ import annotations

import re
import zlib
from datetime import date

import numpy as np
import pandas as pd
import requests

from . import config

CIMIS_BASE = "https://et.water.ca.gov/StationWeb"

def cimis_headers():
    return {
        "Ocp-Apim-Subscription-Key": config.CIMIS_APP_KEY,
        "Accept": "application/json"
    }
CONTRACT = ["station_id", "lat", "lon", "elev_m", "datetime", "temp_F", "source", "is_filled"]


# ---------------------------------------------------------------- stations

def _parse_decimal(hms: str) -> float:
    """CIMIS gives coordinates like "38º32'7N / 38.5353"; take the decimal part."""
    m = re.search(r"/\s*(-?\d+\.\d+)", str(hms))
    return float(m.group(1)) if m else float("nan")


def get_stations() -> pd.DataFrame:
    """Columns: station_id, name, county, lat, lon, elev_m, active."""
    if config.DATA_SOURCE == "synthetic":
        return _synthetic_stations()
    cache = config.CACHE_DIR / "stations.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    r = requests.get(
    f"{CIMIS_BASE}/GetAllStations",
    headers=cimis_headers(),
    timeout=30
)
    r.raise_for_status()
    rows = []
    for s in r.json()["Stations"]:
        rows.append({
            "station_id": str(s["StationNbr"]),
            "name": s.get("Name"),
            "county": s.get("County"),
            "lat": _parse_decimal(s.get("HmsLatitude")),
            "lon": _parse_decimal(s.get("HmsLongitude")),
            "elev_m": float(s.get("Elevation") or 0) * 0.3048,  # CIMIS reports feet
            "active": str(s.get("IsActive")).lower() == "true",
        })
    df = pd.DataFrame(rows)
    df.to_parquet(cache)
    return df


# ---------------------------------------------------------------- hourly data

def get_hourly(station_id: str, start: date, end: date) -> pd.DataFrame:
    """Hourly air temperature for one station, cached per station and range."""
    cache = config.CACHE_DIR / f"hourly_{config.DATA_SOURCE}_{station_id}_{start}_{end}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    if config.DATA_SOURCE == "synthetic":
        df = _synthetic_hourly(station_id, start, end)
    else:
        df = _cimis_hourly(station_id, start, end)
    df = fill_gaps(df)
    df.to_parquet(cache)
    return df


def _cimis_hourly(station_id: str, start: date, end: date) -> pd.DataFrame:
    """Fetch real hourly air temperature from CIMIS."""

    if not config.CIMIS_APP_KEY:
        raise RuntimeError("CIMIS_APP_KEY is missing")

    params = {
        "stationNbrs": station_id,
        "startDate": str(start),
        "endDate": str(end),
        "isHourly": "true",
        "unitOfMeasure": "E",
        "dataItems": "hly-air-tmp",
    }

    r = requests.get(
        f"{CIMIS_BASE}/GetDataByStationNumber",
        params=params,
        headers=cimis_headers(),
        timeout=120,
    )

    r.raise_for_status()

    if "json" not in r.headers.get("Content-Type", "").lower():
        raise RuntimeError(
            f"CIMIS returned non-JSON response: {r.text[:200]}"
        )

    records = []

    for provider in r.json()["Data"]["Providers"]:
        records.extend(provider.get("Records", []))

    if not records:
        return pd.DataFrame(columns=CONTRACT)

    st = get_stations().set_index("station_id").loc[station_id]

    rows = []

    for rec in records:
        hour = int(rec["Hour"]) // 100

        ts = (
            pd.Timestamp(rec["Date"])
            + pd.Timedelta(hours=hour)
        )

        val = (rec.get("HlyAirTmp") or {}).get("Value")

        rows.append({
            "station_id": station_id,
            "lat": st.lat,
            "lon": st.lon,
            "elev_m": st.elev_m,
            "datetime": ts,
            "temp_F": float(val) if val not in (None, "") else np.nan,
            "source": "cimis",
            "is_filled": False,
        })

    return pd.DataFrame(rows)[CONTRACT]


def fill_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """Put every hour on the clock; fill gaps <= MAX_FILL_HOURS, flag what was filled."""
    if df.empty:
        return df
    meta = df.iloc[0][["station_id", "lat", "lon", "elev_m", "source"]]
    s = df.set_index("datetime")["temp_F"].sort_index()
    s = s[~s.index.duplicated()]
    full = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="h"))
    filled = full.interpolate(limit=config.MAX_FILL_HOURS, limit_area="inside")
    out = pd.DataFrame({"datetime": full.index, "temp_F": filled.to_numpy(),
                        "is_filled": (full.isna() & filled.notna()).to_numpy()})
    for k, v in meta.items():
        out[k] = v
    return out[CONTRACT]


# ---------------------------------------------------------------- synthetic data
# Clearly fake. Lets the frontend and the validation code run before CIMIS data lands.

_SYN = [  # station_id, name, county, lat, lon, elev_m  (approximate locations)
    ("6", "Davis", "Yolo", 38.535, -121.776, 18),
    ("12", "Durham (inactive)", "Butte", 39.609, -121.824, 40),
    ("70", "Manteca", "San Joaquin", 37.834, -121.223, 10),
    ("71", "Modesto", "Stanislaus", 37.645, -121.188, 11),
    ("39", "Parlier", "Fresno", 36.598, -119.504, 103),
    ("5", "Shafter", "Kern", 35.532, -119.282, 110),
    ("SYN-A", "Synthetic Orland", "Glenn", 39.75, -122.15, 75),
    ("SYN-B", "Synthetic Biggs", "Butte", 39.42, -121.71, 30),
    ("SYN-C", "Synthetic Colusa", "Colusa", 39.23, -122.02, 18),
    ("SYN-D", "Synthetic Foothill", "Placer", 38.95, -121.10, 450),
]


def _synthetic_stations() -> pd.DataFrame:
    df = pd.DataFrame(_SYN, columns=["station_id", "name", "county", "lat", "lon", "elev_m"])
    df["active"] = df["station_id"] != "12"
    return df


def synthetic_temp(lat, lon, elev_m, index: pd.DatetimeIndex, seed: int = 0) -> np.ndarray:
    """Seasonal + daily cycle + latitude/elevation effects + shared weather + local noise."""
    doy = index.dayofyear.to_numpy()
    hour = index.hour.to_numpy()
    seasonal = 61 - 16 * np.cos(2 * np.pi * (doy - 15) / 365.25)
    daily = 11 * np.sin(2 * np.pi * (hour - 9) / 24)
    days = (index - pd.Timestamp("2000-01-01")).days.to_numpy()  # same weather at every station
    shared = 5 * np.sin(days / 4.3) + 3 * np.sin(days / 9.7)      # regional weather systems
    local = np.random.default_rng(seed).normal(0, 1.2, len(index))
    return seasonal + daily + shared + local - 1.2 * (lat - 38.5) - config.LAPSE_F_PER_M * elev_m


def _synthetic_hourly(station_id: str, start: date, end: date) -> pd.DataFrame:
    st = _synthetic_stations().set_index("station_id").loc[station_id]
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(hours=23), freq="h")
    seed = zlib.crc32(station_id.encode()) % 10_000  # deterministic across runs
    temps = synthetic_temp(st.lat, st.lon, st.elev_m, idx, seed=seed)
    temps[np.random.default_rng(seed).random(len(temps)) < 0.01] = np.nan  # ~1% missing
    return pd.DataFrame({
        "station_id": station_id, "lat": st.lat, "lon": st.lon, "elev_m": st.elev_m,
        "datetime": idx, "temp_F": temps, "source": "synthetic", "is_filled": False,
    })[CONTRACT]
