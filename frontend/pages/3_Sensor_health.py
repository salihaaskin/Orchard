"""Is a weather station's sensor working? Shows /sensor/health, with a fault demo.

Pick a station and a season: we list stretches that look broken (and why) and what the
chill totals are with those hours repaired. "Simulate a fault" breaks good data on purpose
so you can watch it being caught and repaired, next to the real readings.
"""
import os
from datetime import date, datetime, time

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API = os.getenv("BACKEND_URL", "http://localhost:8000")
st.set_page_config(page_title="Sensor health", layout="wide")

FAULTS = {"stuck": "Stuck (same reading repeats)", "drift": "Drift (+6 °C off)",
          "spike": "Spike (one wild reading)", "missing": "Dropout (no readings)"}
LINES = {  # series key -> (label, color, dash); colors match the Source validation page
    "true": ("Real readings", "#2a78d6", "solid"),
    "reported": ("What the sensor sent", "#eb6834", "solid"),
    "repaired": ("Repaired", "#1baf7a", "solid"),
    "neighbors_idw": ("Neighbors (IDW)", "#8a8a8a", "dot"),
}


@st.cache_data(show_spinner=False)
def get(path, **params):
    """GET from the backend; cached so re-running the same check is instant."""
    r = requests.get(f"{API}{path}", params=params, timeout=600)
    r.raise_for_status()
    return r.json()


def series_chart(s: dict) -> go.Figure:
    """Temperatures around the fault, with faulty hours shaded."""
    fig = go.Figure()
    times = pd.to_datetime(s["time"])
    faulty = pd.Series(s["faulty"], index=times)
    for start, block in faulty[faulty].groupby((faulty != faulty.shift()).cumsum()[faulty]):
        fig.add_vrect(x0=block.index[0] - pd.Timedelta(minutes=30), x1=block.index[-1] + pd.Timedelta(minutes=30),
                      fillcolor="#eb6834", opacity=0.12, line_width=0)
    for key, (label, color, dash) in LINES.items():
        if key in s:
            fig.add_trace(go.Scatter(x=times, y=s[key], name=label, mode="lines",
                                     line=dict(color=color, width=2, dash=dash),
                                     hovertemplate=label + "<br>%{x}<br>%{y:.1f} °F<extra></extra>"))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=40, b=10), yaxis_title="°F",
                      title="Around the fault (shaded = flagged)",
                      legend=dict(orientation="h", y=-0.2), hovermode="x unified")
    return fig


st.title("Is the sensor working?")
st.caption("We compare each station with its neighbors to spot broken readings, then replace them "
           "with the fill that came closest when we hid real readings (anchored IDW).")

stations = [s for s in get("/stations") if s.get("active", True)]
labels = {f"{s['name']} ({s['station_id']})": s["station_id"] for s in sorted(stations, key=lambda s: s["name"])}
c1, c2 = st.columns([2, 1])
default = next((i for i, k in enumerate(labels) if k.startswith("Davis")), 0)
station = labels[c1.selectbox("Station", list(labels), index=default)]
as_of = c2.date_input("Season ending", value=date(2025, 2, 28), help="Checks Nov 1 up to this date.")

with st.expander("Simulate a fault (demo)", expanded=True):
    d1, d2, d3, d4 = st.columns(4)
    fault = d1.selectbox("Fault", ["none"] + list(FAULTS), format_func=lambda k: FAULTS.get(k, "No simulation"))
    day = d2.date_input("Starts on", value=date(2025, 1, 20))
    hour = d3.number_input("At hour", min_value=0, max_value=23, value=4)
    hours = d4.number_input("Lasting (hours)", min_value=1, max_value=72, value=24 if fault == "drift" else 8)

if not st.button("Check sensor", type="primary"):
    st.stop()

params = {"station_id": station, "as_of": str(as_of)}
if fault != "none":
    params.update(simulate=fault, simulate_start=datetime.combine(day, time(int(hour))).isoformat(),
                  simulate_hours=int(hours))
with st.spinner("Comparing the station with its neighbors..."):
    r = get("/sensor/health", **params)

if r.get("status") == "no_data":
    st.error(r["message"])
    st.stop()

# ---------------------------------------------------------------- verdict
sim = r.get("simulation")
if sim:
    k1, k2, k3 = st.columns(3)
    k1.metric("Fault caught?", "Yes" if sim["caught"] else "No")
    k2.metric("Error of the broken readings",
              "—" if sim["error_of_broken_readings_C"] is None else f"{sim['error_of_broken_readings_C']:.1f} °C")
    k3.metric("Error after repair", f"{sim['error_after_repair_C']:.2f} °C",
              help="Compared with the real readings we hid.")
if r["status"] == "ok":
    st.success(f"{r['name']}: no faults found in {r['window']}.")
else:
    conf = r["alert_confidence"]
    text = f"{r['name']}: {len(r['episodes'])} faulty stretch(es), {r['faulty_hours']} hours in {r['window']}."
    if conf == "low":
        text += (f" Nearest neighbor is {r['reference']['nearest_neighbor_km']} km away, so these alarms "
                 "are uncertain and the data would not be changed in estimates.")
    st.warning(text)

if "series" in r:
    st.plotly_chart(series_chart(r["series"]), use_container_width=True)

if r["episodes"]:
    st.subheader("Faulty stretches")
    st.dataframe(pd.DataFrame(r["episodes"])[["type", "start", "end", "hours", "detail"]],
                 hide_index=True, use_container_width=True)
for note in r.get("notes", []):
    st.caption(note)

# ---------------------------------------------------------------- effect on chill
st.subheader("Chill this season")
metrics = {"chill_portions": "Chill portions", "chill_hours": "Chill hours (32–45°F)", "utah_units": "Utah units"}
st.dataframe(pd.DataFrame({
    "Metric": list(metrics.values()),
    "As the sensor reported": [r["chill_as_reported"][m] for m in metrics],
    "With faulty hours repaired": [r["chill_repaired"][m] for m in metrics],
}), hide_index=True, use_container_width=True)
fixed = {k: v for k, v in r["repair"]["hours_by_source"].items() if k != "sensor"}
st.caption(f"Repaired hours: {fixed or 'none'} · order tried: {', '.join(r['repair']['order_tried'])}. "
           + r["repair"]["why_this_order"])
