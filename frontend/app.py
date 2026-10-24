"""Chill at My Orchard — grower-facing app. Talks to the backend API only."""
import os
from datetime import date

import folium
import plotly.graph_objects as go
import requests
import streamlit as st
from streamlit_folium import st_folium

API = os.getenv("BACKEND_URL", "http://localhost:8000")
st.set_page_config(page_title="Chill at My Orchard", layout="wide")

METHOD_LABELS = {
    "nearest": "the nearest weather station",
    "idw": "nearby stations, weighted by distance (IDW)",
    "open_meteo": "the Open-Meteo weather model",
    "open_meteo_corrected": "the nearest station, corrected with Open-Meteo for the elevation difference",
}


def with_error(value: float, metric: str, chill_err: dict) -> str:
    """'59 ± 3' when we know the expected error for this metric, else just the number."""
    e = chill_err.get(metric)
    return f"{value:.0f} ± {e['plus_minus']:.0f}" if e else f"{value:.0f}"


@st.cache_data(show_spinner=False)
def get(path, **params):
    r = requests.get(f"{API}{path}", params=params, timeout=300)
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- pick a place
st.title("Chill at My Orchard")
st.caption("Chill and growing degree days for your own orchard, even where the weather station is far away or gone.")

demo = get("/demo")
if "pin" not in st.session_state:
    st.session_state.pin = None

c1, c2, c3 = st.columns([1, 1, 1])
if c1.button(demo["durham"]["label"], use_container_width=True, type="primary"):
    st.session_state.pin = {**demo["durham"], "key": "durham"}
if c2.button(demo["foothill"]["label"], use_container_width=True, type="primary"):
    st.session_state.pin = {**demo["foothill"], "key": "foothill"}
with c3:
    as_of = st.date_input("As of", value=date(2026, 2, 1),
                          help="Chill counting starts Nov 1. Pick a past date to replay a season.")
    crop = st.selectbox("Crop", ["almond", "walnut", "pistachio", "prune"])
    r1, r2 = st.columns(2)
    range_low = r1.number_input("Range low (°F)", value=32.0, step=1.0)
    range_high = r2.number_input("Range high (°F)", value=45.0, step=1.0)

with st.expander("Or drop a pin on your own orchard", expanded=st.session_state.pin is None):
    m = folium.Map(location=[38.6, -121.4], zoom_start=7)
    for s in get("/stations"):
        folium.CircleMarker([s["lat"], s["lon"]], radius=4, tooltip=s["name"],
                            color="#555" if s.get("active", True) else "#c33").add_to(m)
    clicked = st_folium(m, height=380, use_container_width=True, key="map")
    if clicked and clicked.get("last_clicked"):
        ll = clicked["last_clicked"]
        st.session_state.pin = {"lat": ll["lat"], "lon": ll["lng"], "label": "Your pin", "key": "pin"}

pin = st.session_state.pin
if not pin:
    st.info("Pick a demo orchard or drop a pin to start.")
    st.stop()

exclude = [pin["holdout_station"]] if pin.get("holdout_station") else []
with st.spinner("Estimating temperatures at this orchard..."):
    est = get("/estimate", lat=pin["lat"], lon=pin["lon"], as_of=str(as_of), crop=crop, exclude=exclude,
              range_low_f=range_low, range_high_f=range_high)

if est["data_source"] == "synthetic":
    st.warning("Showing SYNTHETIC test data. Set DATA_SOURCE=cimis for real numbers.")

st.subheader(pin["label"])
ch, hist, proj = est["chill"], est["history"], est["projection"]
p = hist["percentiles"].get("chill_portions", {})
unc = est.get("uncertainty") or {}
chill_err = unc.get("chill", {}) if unc.get("available") else {}

# Where the temperatures come from, and why (chosen by testing on stations we hid).
if ch.get("method"):
    st.markdown(f"**Temperatures from:** {METHOD_LABELS.get(ch['method'], ch['method'])}. "
                f"<span style='opacity:0.7'>{ch.get('method_reason', '')}</span>", unsafe_allow_html=True)

# Is the station we lean on working?
alerts = est.get("sensor_alerts") or {}
if alerts.get("status") == "alert":
    kinds = ", ".join(sorted({e["type"] for e in alerts.get("episodes", [])}))
    msg = (f"Station {alerts['station_id']} looks faulty for {alerts['faulty_hours']} hours this season "
           f"({kinds}).")
    if alerts.get("repaired_in_this_estimate"):
        fixed = {k: v for k, v in alerts.get("repair_hours_by_source", {}).items() if k != "sensor"}
        msg += f" Those hours were replaced with estimates ({', '.join(f'{v} h {k}' for k, v in fixed.items())})."
    elif alerts.get("alert_confidence") == "low":
        msg += " Its neighbors are far away, so this alarm is uncertain and the data was not changed."
    st.warning(msg)

# ---------------------------------------------------------------- the three answers
a1, a2, a3 = st.columns(3)
cp_err = chill_err.get("chill_portions")
a1.metric("1 · Chill so far (portions)", with_error(ch["chill_portions"], "chill_portions", chill_err),
          help=f"Since {ch['start']}. Dynamic model, preferred for California's mild winters."
               + (f" Likely error ±{cp_err['plus_minus']:.0f} (range {cp_err['range'][0]:.0f}–"
                  f"{cp_err['range'][1]:.0f}), from stations we hid and estimated." if cp_err else ""))
if p:
    a2.metric("2 · vs. a typical year", f"{ch['chill_portions'] - p['p50']:+.0f}",
              help=f"Typical (median) on this date: {p['p50']}. Normal range {p['p10']}–{p['p90']}.")
if "added" in proj:
    a3.metric("3 · Next 14 days", f"+{proj['added']['chill_portions']:.0f}",
              help=f"Method: {proj['method']}")

cr = ch["custom_range"]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Chill hours (32–45°F)", with_error(ch["chill_hours"], "chill_hours", chill_err))
m2.metric("Hours below 45°F", f"{ch['hours_lt_45']:.0f}")
m3.metric("Utah chill units", with_error(ch["utah_units"], "utah_units", chill_err),
          help="Warm hours subtract units")
m4.metric("GDD since bloom (°C·days)",
          f"{est['gdd']['total_c_days']:.0f}" if est["gdd"]["started"] else "Not yet",
          help=f"Single sine, 5–35°C, counted from {est['gdd']['start']} (assumed bloom date)")

st.markdown(f"**Your temperature range, since {ch['start']}:** "
            f"{cr['hours_below']:.0f} hours at or below {cr['low_f']:.0f}°F · "
            f"{cr['hours_between']:.0f} hours between {cr['low_f']:.0f} and {cr['high_f']:.0f}°F · "
            f"{cr['hours_above']:.0f} hours above {cr['high_f']:.0f}°F")
st.caption(f"{ch['hours_missing']} missing hours · elevation {est['location']['elev_m']} m")

if unc.get("available"):
    with st.expander("How sure are we?"):
        t = unc["hourly_temp_mae_C"]
        st.markdown(
            f"We hid real CIMIS stations, estimated them the same way, and compared. For a spot like "
            f"this the hourly temperature is typically off by **{t['expected']:.1f} °C** "
            f"(range {t['range'][0]:.1f}–{t['range'][1]:.1f}). The ± numbers above are the likely error "
            f"of each chill total so far; the ranges held the real error about 80% of the time.")
        if unc.get("warning"):
            st.warning(unc["warning"])
        st.caption(unc["gdd"])
        mbm = est.get("method_by_metric") or {}
        if mbm:
            st.markdown("**Best source per metric at this orchard** (tested on stations we never used to choose):")
            for metric, r in mbm.items():
                rule = f" · rule: {r['elevation_rule']}" if r.get("elevation_rule") else ""
                st.markdown(f"- {metric.replace('_', ' ')}: {METHOD_LABELS.get(r['method'], r['method'])}{rule}")

# ---------------------------------------------------------------- chart
fig = go.Figure()
for s in hist["seasons"]:
    fig.add_trace(go.Scatter(y=s["curve"], mode="lines", line=dict(color="lightgray", width=1),
                             name=s["season"], hoverinfo="name+y", showlegend=False))
fig.add_trace(go.Scatter(y=ch["curve"], mode="lines", line=dict(color="#1f6f8b", width=4), name="This season"))
fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10),
                  xaxis_title="Days since Nov 1", yaxis_title="Chill portions",
                  title="This season (blue) vs. past seasons (gray)")
st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- proof
station = pin.get("holdout_station")
if station:
    with st.expander("How accurate is this? (we hid the real station and estimated it)", expanded=True):
        v = get("/validate", station_id=station)
        w, n = v["with_elevation_adjustment"], v["no_elevation_adjustment"]
        b1, b2, b3 = st.columns(3)
        b1.metric("Actual chill portions", f"{w['actual']['chill_portions']:.1f}")
        b2.metric("Our estimate", f"{w['estimated']['chill_portions']:.1f}",
                  f"{w['error']['chill_portions']:+.1f} error", delta_color="off")
        b3.metric("Hourly error (RMSE)", f"{w['hourly_rmse_F']} °F")
        st.caption(f"Season {v['season']}. Without elevation adjustment: "
                   f"{n['error']['chill_portions']:+.1f} portions error. "
                   f"Neighbors: {', '.join(x['station_id'] for x in w['neighbors'])}")

st.caption("Data: CIMIS (California DWR), Open-Meteo. Chill models as used by UC Davis FNRIC.")
