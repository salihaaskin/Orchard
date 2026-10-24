"""Shared bits for every page: the backend address, a cached GET, plain names and units."""
import os

import requests
import streamlit as st

API = os.getenv("BACKEND_URL", "http://localhost:8000")

# Plain names a grower understands, one color and marker per method on every chart.
METHODS = {
    "nearest": {"label": "Closest weather station", "color": "#2a78d6", "symbol": "circle"},
    "idw": {"label": "Average of nearby stations", "color": "#eb6834", "symbol": "square"},
    "open_meteo": {"label": "Weather model alone", "color": "#1baf7a", "symbol": "diamond"},
    "open_meteo_corrected": {"label": "Closest station + weather model", "color": "#eda100",
                             "symbol": "triangle-up"},
}

# What each kind of sensor fault means, in words.
FAULTS = {
    "missing": "stopped sending data",
    "stuck": "got stuck on one number",
    "drift": "slowly drifted off",
    "spike": "jumped suddenly",
    "impossible_value": "sent impossible readings",
}

CHART = dict(margin=dict(l=10, r=10, t=50, b=10), height=360, font=dict(size=14),
             legend=dict(orientation="h", y=-0.18))


@st.cache_data(show_spinner=False, ttl=3600)
def get(path: str, **params):
    """GET a backend endpoint and return its JSON; cached for an hour (same question, same answer)."""
    r = requests.get(f"{API}{path}", params=params, timeout=900)
    r.raise_for_status()
    return r.json()


def c_to_f(delta_c: float) -> float:
    """A temperature *difference* in °C as °F (no +32: we convert gaps, not readings)."""
    return delta_c * 9 / 5


def m_to_ft(m: float) -> float:
    """Meters to feet."""
    return m * 3.281


def km_to_mi(km: float) -> float:
    """Kilometers to miles."""
    return km * 0.6214


def nice_date(iso: str) -> str:
    """'2025-11-01' -> 'Nov 1, 2025'."""
    import datetime as dt
    d = dt.date.fromisoformat(str(iso)[:10])
    return f"{d:%b} {d.day}, {d.year}"


def credits():
    """Data credits shown at the bottom of every page."""
    st.caption("Data: CIMIS weather stations (California DWR) · Open-Meteo (ERA5-Land, contains modified "
               "Copernicus Climate Change Service information; place search uses GeoNames) · chill models "
               "as used by UC Davis FNRIC.")
