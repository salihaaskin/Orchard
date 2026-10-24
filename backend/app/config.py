"""Shared settings. Change values here (or via environment variables), not in code."""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load the repo-root .env (CIMIS key, data source). Real environment variables win.
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env", override=False)

# Where cached station data lives (repo-root data/ folder by default).
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# "cimis" = real data from the CIMIS API (needs CIMIS_APP_KEY).
# "synthetic" = generated fake data so the frontend can be built before real data lands.
DATA_SOURCE = os.getenv("DATA_SOURCE", "synthetic")
CIMIS_APP_KEY = os.getenv("CIMIS_APP_KEY", "")

TIMEZONE = "America/Los_Angeles"

# Chill season start (month, day). FNRIC's season starts Sept 1; many growers count
# from Nov 1. We use Nov 1 for growers; set (9, 1) to match FNRIC's chill calculator.
CHILL_START = (int(os.getenv("CHILL_START_MONTH", 11)), 1)

# Default "hours in a range" the grower sees; they can change it in the app.
CUSTOM_RANGE_F = (32.0, 45.0)

# GDD counts from bloom (FNRIC's almond hull split model counts from full bloom date).
# ASSUMPTION: Feb 20 as a typical almond bloom date. Ask FNRIC; let growers enter their own.
GDD_START = (2, 20)
# Single-sine thresholds in °C (lower, upper). FNRIC: 5°C (41°F) and 35°C (95°F).
GDD_THRESHOLDS_C = {"almond": (5.0, 35.0), "walnut": (5.0, 35.0),
                    "pistachio": (5.0, 35.0), "prune": (5.0, 35.0)}  # TODO: confirm non-almond crops

# Interpolation
NEIGHBORS = int(os.getenv("NEIGHBORS", 5))      # stations used for each estimate
IDW_POWER = float(os.getenv("IDW_POWER", 2.0))
# Temperature change per meter of elevation, °F/m (standard lapse 6.5 °C/km ≈ 0.0117 °F/m).
# Winter valley inversions can make this wrong; validation decides the value we ship.
LAPSE_F_PER_M = float(os.getenv("LAPSE_F_PER_M", 0.0117))

# Open-Meteo Historical API model we test as a stand-in for a station (validation).
# era5_land: ~9 km grid, consistent back to 1950, about 5 days behind real time.
OPENMETEO_MODEL = os.getenv("OPENMETEO_MODEL", "era5_land")
# Validation reports the share of hours an estimate lands within this many °C of the station.
WITHIN_C = 2.0

# Source selection: leave-one-station-out results it learns from (built by
# backend/scripts/build_validation_table.py), and how many similar cases it averages.
VALIDATION_TABLE = DATA_DIR / "validation" / "loso_table.csv"
SELECT_K = int(os.getenv("SELECT_K", 7))
# Switch away from the usual best method only if another one won by at least this much (°C)
# at the similar stations (smaller differences over a handful of stations are mostly noise).
SELECT_MIN_MARGIN_C = float(os.getenv("SELECT_MIN_MARGIN_C", 0.15))

# Gaps up to this many hours are filled by linear interpolation; longer gaps stay missing.
MAX_FILL_HOURS = 3

# Sensor health checks (sensor_health.py). "Reference" = the estimate from neighbors, which
# doesn't use the station itself. Thresholds were checked against healthy CIMIS records.
SENSOR_RANGE_F = (-10.0, 125.0)      # outside this is not a real California air temperature
SENSOR_STUCK_HOURS = 6               # identical readings this long... (healthy max seen: 7, once)
SENSOR_STUCK_REF_RANGE_C = 2.0       # ...while the reference moves at least this much = stuck
SENSOR_SPIKE_F = 15.0                # one-hour jump this big while the reference stays put
# Drift: the station's usual difference from the reference (median of the past week) is taken
# out first, then a day-long average change bigger than this is flagged. On 68 healthy
# station-seasons this gave ~0.5 alarms per season and caught 67 of 68 planted +6 °C drifts.
SENSOR_BASELINE_DAYS = 7
SENSOR_DEVIATION_HOURS = 24
SENSOR_DEVIATION_C = float(os.getenv("SENSOR_DEVIATION_C", 4.0))
# Alarms are "low confidence" when the nearest neighbor (the reference) is farther than this.
SENSOR_REFERENCE_MAX_KM = 50.0
# Replace faulty hours (and gaps longer than MAX_FILL_HOURS) with the tested fill in /estimate.
# On by default (team decision): only for high-confidence alarms, and every repaired hour is
# labeled and counted. Set SENSOR_REPAIR=false to keep long gaps missing instead.
SENSOR_REPAIR = os.getenv("SENSOR_REPAIR", "true").lower() == "true"

# Preset demo orchards. Coordinates are placeholders until the team picks real spots.
DEMO_ORCHARDS = {
    "durham": {
        "label": "Durham, Butte County (station removed)",
        "lat": 39.61, "lon": -121.82,
        "holdout_station": "12",  # Durham CIMIS station, stopped reporting July 2025
    },
    "foothill": {
        "label": "Foothill orchard (elevation)",
        "lat": 38.90, "lon": -121.05,
        "holdout_station": None,  # TODO: pick a foothill CIMIS station from the station list
    },
}
