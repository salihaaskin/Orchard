"""Past hourly temperatures from the Open-Meteo Historical Weather API (free, no key).

This is a candidate data source: a weather-model value for any coordinate, even where
there is no station. /validate compares it against real CIMIS readings to find out how
far we can trust it. Finished date ranges are cached in data/cache/ for offline demos.
"""
from __future__ import annotations

import json
import zlib
from datetime import date, timedelta

import pandas as pd
import requests

from . import config, data

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# CIMIS timestamps are Pacific Standard Time all year (no daylight saving): UTC - 8 h.
PST_OFFSET = pd.Timedelta(hours=-8)

# ERA5-Land runs about 5 days behind real time; don't cache a range that may still fill in.
ARCHIVE_DELAY_DAYS = 7

# Synthetic mode only: the fake "model" is this much warmer than the fake stations.
SYNTHETIC_MODEL_BIAS_F = 1.0


def hourly_archive(lat: float, lon: float, start: date, end: date,
                   elev_m: float | None = None, model: str | None = None) -> pd.Series:
    """Hourly °F at (lat, lon) from start 00:00 to end 23:00, on CIMIS's clock.

    The index is naive Pacific Standard Time, the same as data.get_hourly(), so the two
    can be lined up hour by hour. elev_m, if given, tells Open-Meteo the exact height to
    correct its temperature to (otherwise it uses its own 90 m terrain map).
    Returns an empty series if the API can't be reached and nothing is cached.
    """
    if config.DATA_SOURCE == "synthetic":
        return _synthetic_archive(lat, lon, start, end, elev_m)

    model = model or config.OPENMETEO_MODEL
    cache = config.CACHE_DIR / f"openmeteo_{model}_{lat:.3f}_{lon:.3f}_{start}_{end}.json"
    if cache.exists():
        payload = json.loads(cache.read_text())
    else:
        params = {
            "latitude": lat, "longitude": lon,
            # Ask one day extra on each side: shifting UTC to PST moves 8 hours across midnight.
            "start_date": str(start - timedelta(days=1)), "end_date": str(end + timedelta(days=1)),
            "hourly": "temperature_2m", "temperature_unit": "fahrenheit",
            "timezone": "GMT", "models": model,
        }
        if elev_m is not None:
            params["elevation"] = elev_m
        try:
            r = requests.get(ARCHIVE_URL, params=params, timeout=60)
            r.raise_for_status()
            payload = r.json()
        except Exception:
            return pd.Series(dtype=float)
        if end <= date.today() - timedelta(days=ARCHIVE_DELAY_DAYS):
            cache.write_text(json.dumps(payload))

    h = payload["hourly"]
    utc = pd.to_datetime(h["time"])
    s = pd.Series(h["temperature_2m"], index=utc + PST_OFFSET, dtype=float)  # None -> NaN
    return _clip(s, start, end)


def _clip(s: pd.Series, start: date, end: date) -> pd.Series:
    """Keep start 00:00 through end 23:00."""
    lo, hi = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(hours=23)
    return s[(s.index >= lo) & (s.index <= hi)]


def _synthetic_archive(lat, lon, start, end, elev_m) -> pd.Series:
    """Clearly fake model data so tests and the demo run without internet.

    Same regional weather as the fake stations, but its own random noise and a warm bias,
    so it scores like an imperfect model rather than a copy of the station.
    """
    idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(hours=23), freq="h")
    seed = zlib.crc32(f"openmeteo{lat:.3f}{lon:.3f}".encode()) % 10_000
    temps = data.synthetic_temp(lat, lon, elev_m or 0.0, idx, seed=seed) + SYNTHETIC_MODEL_BIAS_F
    return pd.Series(temps, index=idx, dtype=float)
