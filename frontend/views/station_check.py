"""Is my weather station working? Problems we found, in plain words, and a demo that breaks one.

Uses GET /sensor/health: each station is compared hour by hour with its neighbors; broken stretches
are listed with what went wrong and repaired with the fill that tested best.
"""
from datetime import date, datetime, time

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from views.common import FAULTS, c_to_f, credits, get, nice_date

DEMO_FAULTS = {"stuck": "Stuck on one number", "drift": "Slowly drifting off (+11 °F)",
               "spike": "One wild reading", "missing": "Stopped sending data"}
LINES = {  # series key -> (label, color, dash)
    "true": ("What it really was", "#2a78d6", "solid"),
    "reported": ("What the station sent", "#eb6834", "solid"),
    "repaired": ("Our repair", "#2f7d4f", "solid"),
    "neighbors_idw": ("Nearby stations", "#8a8a8a", "dot"),
}


def chart(s: dict) -> go.Figure:
    """Temperatures around the problem, with the flagged hours shaded."""
    fig = go.Figure()
    times = pd.to_datetime(s["time"])
    flagged = pd.Series(s["faulty"], index=times)
    for _, block in flagged[flagged].groupby((flagged != flagged.shift()).cumsum()[flagged]):
        fig.add_vrect(x0=block.index[0] - pd.Timedelta(minutes=30), x1=block.index[-1] + pd.Timedelta(minutes=30),
                      fillcolor="#eb6834", opacity=0.12, line_width=0)
    for key, (label, color, dash) in LINES.items():
        if key in s:
            fig.add_trace(go.Scatter(x=times, y=s[key], name=label, mode="lines",
                                     line=dict(color=color, width=3 if key != "neighbors_idw" else 2, dash=dash),
                                     hovertemplate=label + "<br>%{x|%b %d %H:%M}<br>%{y:.0f} °F<extra></extra>"))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=50, b=10), yaxis_title="temperature (°F)",
                      title="The shaded hours are the ones we flagged and repaired",
                      legend=dict(orientation="h", y=-0.2), hovermode="x unified")
    return fig


st.title("🛠️ Is my weather station working?")
st.markdown("Weather stations sometimes break: they **stop sending data**, get **stuck on one number**, "
            "**slowly drift off**, or **jump suddenly**. A broken station quietly ruins your chill count. "
            "We compare every station with its neighbors, hour by hour, to catch this — and fill the broken "
            "hours with our best estimate.")

stations = [s for s in get("/stations") if s.get("active", True)]
labels = {f"{s['name']} (station {s['station_id']})": s["station_id"]
          for s in sorted(stations, key=lambda s: s["name"])}
c1, c2 = st.columns([2, 1])
default = next((i for i, k in enumerate(labels) if k.startswith("Davis")), 0)
station = labels[c1.selectbox("Weather station", list(labels), index=default)]
as_of = c2.date_input("Winter ending", value=date(2025, 2, 28), help="We check from Nov 1 up to this date.")

with st.expander("Demo: break this station on purpose and see what happens", expanded=True):
    d1, d2, d3, d4 = st.columns(4)
    fault = d1.selectbox("What goes wrong", ["none"] + list(DEMO_FAULTS),
                         format_func=lambda k: DEMO_FAULTS.get(k, "Nothing (just check it)"))
    day = d2.date_input("Starting on", value=date(2025, 1, 20))
    hour = d3.number_input("At hour (0–23)", min_value=0, max_value=23, value=4)
    hours = d4.number_input("For how many hours", min_value=1, max_value=72, value=24 if fault == "drift" else 8)

if not st.button("Check this station", type="primary"):
    st.stop()

params = {"station_id": station, "as_of": str(as_of)}
if fault != "none":
    params.update(simulate=fault, simulate_start=datetime.combine(day, time(int(hour))).isoformat(),
                  simulate_hours=int(hours))
with st.spinner("Comparing the station with its neighbors…"):
    r = get("/sensor/health", **params)

if r.get("status") == "no_data":
    st.error("This station sent no data for that winter.")
    st.stop()

start, end = r["window"].split(" to ")
period = f"{nice_date(start)} – {nice_date(end)}"

# ---------------------------------------------------------------- the demo result
sim = r.get("simulation")
if sim:
    k1, k2, k3 = st.columns(3)
    k1.metric("Did we catch it?", "Yes ✅" if sim["caught"] else "No ❌")
    broken = sim["error_of_broken_readings_C"]
    k2.metric("How wrong the broken readings were",
              "no readings" if broken is None else f"{c_to_f(broken):.0f} °F off")
    k3.metric("How close our repair got", f"{c_to_f(sim['error_after_repair_C']):.1f} °F off",
              help="Compared with the real readings we hid for the demo.")

# ---------------------------------------------------------------- verdict
if r["status"] == "ok":
    st.success(f"✅ **{r['name']}** looks healthy for {period}. No problems found.")
else:
    kinds = sorted({FAULTS.get(e["type"], e["type"]) for e in r["episodes"]})
    text = (f"⚠️ **{r['name']}** had problems **{len(r['episodes'])} time(s)**, {r['faulty_hours']} hours in "
            f"total, between {period}: it {', '.join(kinds)}.")
    if r["alert_confidence"] == "low":
        text += (f" Its nearest neighbor is {r['reference']['nearest_neighbor_km']:.0f} km away, so we're not sure "
                 "these are real problems and we leave its readings as they are.")
    else:
        text += " We fill those hours with our best estimate when we count chill."
    st.warning(text)

if "series" in r:
    st.plotly_chart(chart(r["series"]), use_container_width=True)

if r["episodes"]:
    st.subheader("When it happened")
    st.dataframe(pd.DataFrame([{
        "From": pd.Timestamp(e["start"]).strftime("%b %d, %H:%M"),
        "To": pd.Timestamp(e["end"]).strftime("%b %d, %H:%M"),
        "Hours": e["hours"],
        "What happened": FAULTS.get(e["type"], e["type"]).capitalize(),
    } for e in r["episodes"]]), hide_index=True, use_container_width=True)
if r.get("notes"):
    st.caption("A long flat stretch was left alone because nearby stations were flat too (likely fog), "
               "which is normal.")

# ---------------------------------------------------------------- effect on chill
st.subheader("What it does to the chill count")
show = {"chill_portions": "Chill portions", "chill_hours": "Chill hours (32–45 °F)"}
cols = st.columns(len(show))
for col, (key, label) in zip(cols, show.items()):
    before, after = r["chill_as_reported"][key], r["chill_repaired"][key]
    col.metric(label, f"{after:.0f} after repair", f"{after - before:+.0f} vs. as sent", delta_color="off")
st.caption(f"Counted from {nice_date(start)}.")

with st.expander("For technical readers"):
    st.markdown(f"""
Reference: IDW from the station's neighbors {', '.join(r['reference']['neighbors'])}
(nearest {r['reference']['nearest_neighbor_km']} km). Checks: gaps > 3 h, values outside −10…125 °F,
≥ 6 h identical readings while the reference moves ≥ 2 °C, one-hour jumps ≥ 15 °F the reference doesn't show,
and a 24-hour drift > 4 °C from the station's usual difference to its neighbors (7-day median).
Repair order: {', '.join(r['repair']['order_tried'])}. {r['repair']['why_this_order']}
""")
    if r["episodes"]:
        st.dataframe(pd.DataFrame(r["episodes"]), hide_index=True, use_container_width=True)

credits()
