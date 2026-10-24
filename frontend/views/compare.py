"""Which weather data works best near me? Region-by-region results of hiding real stations.

Uses GET /validate/all: every station near a point is hidden in turn and estimated four ways.
"""
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from views.common import METHODS, c_to_f, credits, get, km_to_mi, m_to_ft

REGIONS = {
    "Sacramento Valley (around Durham / Chico)": (39.61, -121.82),
    "San Joaquin Valley (around Fresno)": (36.60, -119.50),
    "Central Coast (around Salinas)": (36.67, -121.65),
    "Somewhere else": None,
}


def distance_chart(df: pd.DataFrame) -> go.Figure:
    """One dot per station per way: how far its closest neighbor is vs. how far off each way was."""
    fig = go.Figure()
    for key, m in METHODS.items():
        fig.add_trace(go.Scatter(
            x=km_to_mi(df["nearest_dist_km"]), y=c_to_f(df[f"mae_C_{key}"]), mode="markers", name=m["label"],
            marker=dict(color=m["color"], symbol=m["symbol"], size=12, line=dict(width=2, color="white")),
            customdata=df[["name"]],
            hovertemplate="<b>%{customdata[0]}</b><br>" + m["label"]
                          + "<br>off by %{y:.1f} °F on average<br>closest other station %{x:.0f} miles<extra></extra>"))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=50, b=10),
                      title="Farther from the next station = harder to estimate",
                      xaxis_title="miles to the closest other station",
                      yaxis_title="hourly temperature, off by (°F)", yaxis_rangemode="tozero",
                      legend=dict(orientation="h", y=-0.2))
    return fig


st.title("📍 Which weather data works best near me?")
st.markdown("Pick an area. We hide each weather station there, one at a time, estimate its temperatures "
            "four ways, and see which way came closest to what the station really measured.")

c1, c2 = st.columns([2, 1])
region = c1.selectbox("Area", list(REGIONS))
if REGIONS[region] is None:
    lat = c1.number_input("Latitude", value=38.90, format="%.3f")
    lon = c1.number_input("Longitude", value=-121.05, format="%.3f")
else:
    lat, lon = REGIONS[region]
as_of = c2.date_input("Winter ending", value=date(2025, 2, 28), help="Nov 1 up to this date.")

if not st.button("Compare", type="primary"):
    st.info("The first look at a new area downloads a winter of data (a few minutes). After that it's instant.")
    st.stop()

with st.spinner("Hiding each station and checking every way…"):
    v = get("/validate/all", as_of=str(as_of), lat=lat, lon=lon, radius_km=150, max_stations=12)

if v["data_source"] == "synthetic":
    st.warning("Showing made-up TEST data. Set DATA_SOURCE=cimis for real numbers.")
if not v["stations"]:
    st.error("No weather stations with enough data in this area and winter.")
    st.stop()

df = pd.DataFrame(v["stations"])
n = v["stations_compared"]
best = df["best_method_by_mae"].value_counts()
winner = METHODS[best.index[0]]["label"]
# For chill, judge by the average miss (a method can "win" a few stations yet miss badly elsewhere).
cp_mean = {m: df[f"chill_portions_error_{m}"].abs().mean() for m in METHODS}
cp_best = min(cp_mean, key=cp_mean.get)

st.success(f"**In this area, for hour-by-hour temperature: {winner.lower()}** worked best at {best.iloc[0]} of "
           f"{n} stations. **For counting chill: {METHODS[cp_best]['label'].lower()}** was closest on average "
           f"(off by {cp_mean[cp_best]:.1f} chill portions per winter).")
k1, k2, k3 = st.columns(3)
k1.metric("Stations tested", n)
k2.metric("Weather model alone beat the closest station", f"{v['open_meteo_beats_nearest']} of {n}")
k3.metric("Station + weather model beat the closest station", f"{v['open_meteo_corrected_beats_nearest']} of {n}")

st.plotly_chart(distance_chart(df), use_container_width=True)

st.subheader("Station by station")
table = pd.DataFrame({
    "Station": df["name"],
    "Miles to closest other station": km_to_mi(df["nearest_dist_km"]).round(0),
    "Height difference (ft)": m_to_ft(df["nearest_elev_diff_m"]).abs().round(0),
    **{f"{m['label']}: off by (°F)": c_to_f(df[f"mae_C_{k}"]).round(1) for k, m in METHODS.items()},
    "Best for temperature": df["best_method_by_mae"].map(lambda k: METHODS[k]["label"]),
})
st.dataframe(table, hide_index=True, use_container_width=True)
if v["skipped"]:
    st.caption("Left out (not enough data that winter): " + ", ".join(s["station_id"] for s in v["skipped"]))

with st.expander("For technical readers"):
    st.markdown("Leave-one-station-out over the chill window. MAE of hourly temperature on the same hours for "
                "all methods; chill-portion error is the season-total difference. "
                f"Open-Meteo model: {v['open_meteo_model']}. {v['note']}")
    st.dataframe(pd.DataFrame(v["by_nearest_distance"]), hide_index=True, use_container_width=True)

credits()
