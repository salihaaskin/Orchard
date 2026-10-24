"""Can Open-Meteo stand in for a weather station? Shows /validate/all results.

Streamlit turns every file in frontend/pages/ into its own page in the sidebar,
so this page doesn't change the main grower app (app.py).
"""
import os
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API = os.getenv("BACKEND_URL", "http://localhost:8000")
st.set_page_config(page_title="Source validation", layout="wide")

# Same order and colors everywhere on this page; marker shapes so color is never the only cue.
METHODS = {
    "nearest": {"label": "Nearest station", "color": "#2a78d6", "symbol": "circle"},
    "idw": {"label": "IDW + elevation", "color": "#eb6834", "symbol": "square"},
    "open_meteo": {"label": "Open-Meteo (raw)", "color": "#1baf7a", "symbol": "diamond"},
    "open_meteo_corrected": {"label": "Open-Meteo (corrected)", "color": "#eda100", "symbol": "triangle-up"},
}
REGIONS = {
    "Sacramento Valley (around Durham)": (39.61, -121.82),
    "San Joaquin Valley (around Parlier / Fresno)": (36.60, -119.50),
    "Custom point": None,
}


@st.cache_data(show_spinner=False)
def get(path, **params):
    """GET from the backend; cached so switching back to a region is instant."""
    r = requests.get(f"{API}{path}", params=params, timeout=900)
    r.raise_for_status()
    return r.json()


def mae_vs_distance_chart(df: pd.DataFrame) -> go.Figure:
    """One dot per station per method: how far its nearest neighbor is vs. how wrong each method was."""
    fig = go.Figure()
    for key, m in METHODS.items():
        fig.add_trace(go.Scatter(
            x=df["nearest_dist_km"], y=df[f"mae_C_{key}"], mode="markers", name=m["label"],
            marker=dict(color=m["color"], symbol=m["symbol"], size=11, line=dict(width=2, color="white")),
            customdata=df[["name", "nearest_elev_diff_m"]],
            hovertemplate="<b>%{customdata[0]}</b><br>" + m["label"]
                          + "<br>MAE %{y:.2f} °C<br>nearest station %{x:.1f} km away"
                          + "<br>elevation difference %{customdata[1]:.0f} m<extra></extra>",
        ))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=40, b=10),
                      title="Hourly error vs. distance to the nearest other station",
                      xaxis_title="Distance to nearest station (km)",
                      yaxis_title="Mean absolute error (°C)", yaxis_rangemode="tozero",
                      legend=dict(orientation="h", y=-0.2))
    return fig


def mae_by_group_chart(groups: pd.DataFrame) -> go.Figure:
    """Average MAE per method inside each distance group."""
    fig = go.Figure()
    labels = [f"{g} km (n={n})" for g, n in zip(groups["dist_km"], groups["n_stations"])]
    for key, m in METHODS.items():
        fig.add_trace(go.Bar(
            x=labels, y=groups[f"mean_mae_C_{key}"], name=m["label"], marker_color=m["color"],
            marker_line=dict(width=2, color="white"),
            hovertemplate=m["label"] + "<br>%{x}<br>mean MAE %{y:.2f} °C<extra></extra>",
        ))
    fig.update_layout(barmode="group", height=420, margin=dict(l=10, r=10, t=40, b=10),
                      title="Average hourly error by distance group",
                      yaxis_title="Mean absolute error (°C)", legend=dict(orientation="h", y=-0.2))
    return fig


# ---------------------------------------------------------------- controls
st.title("Can Open-Meteo replace a weather station?")
st.caption("We hide one real CIMIS station at a time, estimate its temperature four ways, "
           "and compare each estimate with what the station actually measured.")

c1, c2, c3 = st.columns([2, 1, 1])
region = c1.selectbox("Region", list(REGIONS))
if REGIONS[region] is None:
    lat = c1.number_input("Latitude", value=38.90, format="%.3f")
    lon = c1.number_input("Longitude", value=-121.05, format="%.3f")
else:
    lat, lon = REGIONS[region]
as_of = c2.date_input("Season ending", value=date(2025, 2, 28),
                      help="Chill season from Nov 1 up to this date.")
max_stations = c3.number_input("Stations", min_value=3, max_value=30, value=12,
                               help="Active stations nearest to the region center.")
radius_km = c3.number_input("Radius (km)", min_value=20, max_value=400, value=150, step=10)

if not st.button("Run comparison", type="primary"):
    st.info("First run for a new region downloads a season of CIMIS and Open-Meteo data "
            "(a few minutes). After that it is cached.")
    st.stop()

with st.spinner("Hiding each station and scoring the four methods..."):
    v = get("/validate/all", as_of=str(as_of), lat=lat, lon=lon,
            radius_km=radius_km, max_stations=int(max_stations))

if v["data_source"] == "synthetic":
    st.warning("Showing SYNTHETIC test data. Set DATA_SOURCE=cimis for real numbers.")
if not v["stations"]:
    st.error("No stations with enough data in this region and season.")
    st.stop()

df = pd.DataFrame(v["stations"])
n = v["stations_compared"]

# ---------------------------------------------------------------- headline numbers
k1, k2, k3, k4 = st.columns(4)
k1.metric("Stations compared", n, help=f"Season {v['season']}")
k2.metric("Raw Open-Meteo beats nearest station", f"{v['open_meteo_beats_nearest']} of {n}")
k3.metric("Corrected Open-Meteo beats nearest station", f"{v['open_meteo_corrected_beats_nearest']} of {n}")
best = df["best_method_by_mae"].value_counts()
k4.metric(f"Most often best ({best.iloc[0]} of {n})", METHODS[best.index[0]]["label"],
          help="Method with the lowest hourly MAE at the most stations")

# ---------------------------------------------------------------- charts
st.plotly_chart(mae_vs_distance_chart(df), use_container_width=True)
groups = pd.DataFrame(v["by_nearest_distance"])
if not groups.empty:
    st.plotly_chart(mae_by_group_chart(groups), use_container_width=True)

# ---------------------------------------------------------------- table
st.subheader("Per station")
table = pd.DataFrame({
    "Station": df["name"] + " (" + df["station_id"] + ")",
    "Elevation (m)": df["elev_m"],
    "Nearest station": df["nearest_station_id"],
    "Distance (km)": df["nearest_dist_km"],
    "Elevation diff (m)": df["nearest_elev_diff_m"],
    **{f"MAE °C · {m['label']}": df[f"mae_C_{k}"] for k, m in METHODS.items()},
    "Open-Meteo bias °C": df["bias_C_open_meteo"],
    **{f"Chill portions error · {m['label']}": df[f"chill_portions_error_{k}"] for k, m in METHODS.items()},
    "Best (lowest MAE)": df["best_method_by_mae"].map(lambda k: METHODS[k]["label"]),
})
st.dataframe(table, hide_index=True, use_container_width=True)

if v["skipped"]:
    st.caption("Skipped: " + "; ".join(f"{s['station_id']} ({s['reason']})" for s in v["skipped"]))

with st.expander("What the four methods are"):
    st.markdown(
        "- **Nearest station**: the closest other CIMIS station's reading, unchanged.\n"
        "- **IDW + elevation**: nearby stations weighted by distance, adjusted for elevation.\n"
        f"- **Open-Meteo (raw)**: the Open-Meteo historical model (`{v['open_meteo_model']}`) "
        "at the station's coordinates.\n"
        "- **Open-Meteo (corrected)**: the nearest station's reading plus the temperature "
        "difference Open-Meteo sees between the two places that hour.\n\n"
        "MAE is the average size of the hourly error. Bias is the average signed error "
        "(+ means the estimate runs warm). All methods are scored on the same hours.")
st.caption(v["note"] + " Data: CIMIS (California DWR), Open-Meteo (ERA5-Land, contains modified "
           "Copernicus Climate Change Service information).")
