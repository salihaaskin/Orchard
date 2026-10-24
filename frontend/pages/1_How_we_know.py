"""How we know: the evidence behind every number, as a story for growers and judges.

Everything here comes from GET /evidence: real CIMIS stations we hid and estimated
(leave-one-station-out), the elevation rule, the error bars, and the sensor tests.
"""
import os

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API = os.getenv("BACKEND_URL", "http://localhost:8000")
st.set_page_config(page_title="How we know", layout="wide")

# One color and shape per method on every chart of the app (same as Source validation).
METHODS = {
    "nearest": {"label": "Nearest station", "color": "#2a78d6", "symbol": "circle"},
    "idw": {"label": "IDW + elevation", "color": "#eb6834", "symbol": "square"},
    "open_meteo": {"label": "Open-Meteo (raw)", "color": "#1baf7a", "symbol": "diamond"},
    "open_meteo_corrected": {"label": "Open-Meteo, corrected", "color": "#eda100", "symbol": "triangle-up"},
}
CHART = dict(margin=dict(l=10, r=10, t=50, b=10), height=360,
             font=dict(size=14), legend=dict(orientation="h", y=-0.18))


@st.cache_data(show_spinner=False, ttl=3600)
def get(path, **params):
    """GET from the backend; cached for an hour."""
    r = requests.get(f"{API}{path}", params=params, timeout=300)
    r.raise_for_status()
    return r.json()


def method_bars(values: dict, title: str, unit: str) -> go.Figure:
    """Horizontal bars, one per method, best (smallest) marked."""
    order = sorted(values, key=values.get, reverse=True)
    best = min(values, key=values.get)
    fig = go.Figure(go.Bar(
        y=[METHODS[m]["label"] for m in order], x=[values[m] for m in order], orientation="h",
        marker_color=[METHODS[m]["color"] for m in order],
        text=[f"{values[m]:.2f}" + ("  ← best" if m == best else "") for m in order],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:.2f} " + unit + "<extra></extra>"))
    fig.update_layout(title=title, xaxis_title=f"average error ({unit})", **CHART)
    fig.update_layout(height=280, showlegend=False, margin=dict(l=10, r=60, t=50, b=10))
    return fig


ev = get("/evidence")
if not ev.get("available"):
    st.error("The validation results are not built yet (data/validation/loso_table.csv).")
    st.stop()
s, rule_cp, sens = ev["summary"], ev["elevation_rule"]["chill_portions"], ev["sensor"]
err_temp = ev["error_models"]["hourly_temp"]

# ---------------------------------------------------------------- hero
st.title("How we know our numbers are right")
st.markdown(
    "A grower's chill estimate is only useful if they know how far to trust it. So we didn't assume "
    "which weather source is best. **We hid real CIMIS stations, estimated them every way we could, and "
    "measured the error** — then let those measurements pick the method and size the error bars.")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Hidden-station tests", f"{s['cases']}",
          help=f"{s['stations']} CIMIS stations × {len(s['seasons'])} winters, {len(s['regions'])} regions")
k2.metric("Chill-portion error vs. old method",
          f"−{(1 - rule_cp['elevation_rule_mean_error'] / rule_cp['always_method_mean_error']['idw']) * 100:.0f}%",
          help="Elevation rule vs. always using IDW, on stations the rule never saw")
cov = err_temp["scores"][err_temp["best_model"]]["range_coverage_pct"]
k3.metric("Error bars that hold", f"{cov:.0f}%", help="Our ~80% ranges contained the real error this often, "
          "on stations they were not fitted to")
k4.metric("Sensor drifts caught", f"{sens.get('caught_pct', {}).get('drift', 0):.0f}%",
          help=f"with {sens.get('alarms_per_station_season', 0):.2f} false-looking alarms per station per season")

# ---------------------------------------------------------------- 1. the test
st.header("1 · The test: pretend a station doesn't exist")
c1, c2 = st.columns([3, 2])
with c1:
    df = pd.DataFrame(ev["stations"])
    df["best_chill"] = df[[f"chill_portions_error_{m}" for m in METHODS]].abs().idxmin(axis=1).str.replace(
        "chill_portions_error_", "", regex=False)
    first = df.drop_duplicates("station_id")
    fig = go.Figure()
    for key, m in METHODS.items():
        sub = first[first["best_chill"] == key]
        if sub.empty:
            continue
        fig.add_trace(go.Scattermapbox(
            lat=sub["lat"], lon=sub["lon"], mode="markers", name=m["label"],
            marker=dict(size=13, color=m["color"]),
            customdata=sub[["name", "nearest_dist_km", "nearest_elev_diff_m"]],
            hovertemplate="<b>%{customdata[0]}</b><br>nearest other station %{customdata[1]:.0f} km"
                          "<br>elevation difference %{customdata[2]:.0f} m<extra>" + m["label"] + "</extra>"))
    fig.update_layout(mapbox=dict(style="open-street-map", center=dict(lat=37.6, lon=-121.0), zoom=5.3),
                      title=f"{s['stations']} test stations, colored by the method that best estimated their chill",
                      **{**CHART, "height": 460})
    st.plotly_chart(fig, use_container_width=True)
with c2:
    st.markdown(f"""
For each of **{s['stations']} stations** in the Sacramento Valley, San Joaquin Valley and Central Coast,
over the winters ending {', '.join(x[:4] for x in s['seasons'])}:

1. **Hide** the station's own readings.
2. **Estimate** its hourly temperature four ways:
   - <span style='color:{METHODS['nearest']['color']}'>●</span> the **nearest** other station
   - <span style='color:{METHODS['idw']['color']}'>■</span> **IDW**: nearby stations weighted by distance, adjusted for elevation
   - <span style='color:{METHODS['open_meteo']['color']}'>◆</span> **Open-Meteo** weather model ({s['open_meteo_model']})
   - <span style='color:{METHODS['open_meteo_corrected']['color']}'>▲</span> **Open-Meteo, corrected**: the nearest station plus the temperature difference Open-Meteo sees between the two places
3. **Compare** with what the station really measured: hourly temperature, chill portions, chill hours, Utah units.

Removing a station is exactly what happened at **Durham** (station 12, closed in 2025).
""", unsafe_allow_html=True)

# ---------------------------------------------------------------- 2. what you measure decides
st.header("2 · The best temperature source isn't the best chill source")
me = ev["method_errors"]
c1, c2 = st.columns(2)
c1.plotly_chart(method_bars(me["Hourly temperature (°C)"], "Hourly temperature error", "°C"),
                use_container_width=True)
c2.plotly_chart(method_bars(me["Chill portions"], "Season chill-portion error", "portions"),
                use_container_width=True)
st.markdown(
    "IDW averages several stations, which smooths the coldest night hours and the warmest afternoons. "
    "That gives the **smallest temperature error** but shifts how many hours fall inside the chill "
    "bands, so it **miscounts chill**. A single real station keeps the true day-night swing. "
    "Raw Open-Meteo was the least accurate on its own (it runs warm in the valleys' winter fog), "
    "so we only use it *corrected by a real station*.")

# ---------------------------------------------------------------- 3. elevation
st.header("3 · Elevation changes the answer — and that's where Open-Meteo earns its place")
be = pd.DataFrame(ev["by_elevation"])
fig = go.Figure()
for key, m in METHODS.items():
    fig.add_trace(go.Bar(
        x=[f"{r['elevation_difference']}<br>({r['stations']} stations)" for r in ev["by_elevation"]],
        y=[r["chill_portions"][key] for r in ev["by_elevation"]], name=m["label"], marker_color=m["color"],
        marker_line=dict(width=2, color="white"),
        hovertemplate=m["label"] + "<br>%{x}<br>%{y:.2f} portions<extra></extra>"))
fig.add_vline(x=1.5, line_dash="dash", line_color="#888")
fig.add_annotation(x=1.5, y=1.02, yref="paper", text="rule switches at 50 m", showarrow=False,
                   font=dict(color="#666"))
fig.update_layout(barmode="group", title="Season chill-portion error by elevation difference to the nearest station",
                  yaxis_title="average error (portions)", **{**CHART, "height": 420})
st.plotly_chart(fig, use_container_width=True)

c1, c2 = st.columns([2, 3])
with c1:
    rule_text = rule_cp["rule_learned_most_often"].replace("open_meteo_corrected", "corrected Open-Meteo")
    st.markdown(f"""
**The rule the data chose:** `{rule_text}`

Learned {rule_cp['rule_stability']} times when each station was left out —
the threshold wasn't picked by us, it was picked by the data, the same way every time.

Below 50 m, valley stations share the same air, so the nearest real station wins.
Above 50 m, a fixed elevation correction goes wrong in winter inversions, while Open-Meteo
models the difference between the two places hour by hour.
""")
with c2:
    a = rule_cp["always_method_mean_error"]
    t = pd.DataFrame({
        "Strategy (on stations it never saw)": ["Elevation rule", "Always nearest station", "Always IDW (before)",
                                                "Always corrected Open-Meteo", "Always raw Open-Meteo"],
        "Chill-portion error": [rule_cp["elevation_rule_mean_error"], a["nearest"], a["idw"],
                                a["open_meteo_corrected"], a["open_meteo"]],
    })
    st.dataframe(t.style.format({"Chill-portion error": "{:.2f}"}).highlight_min(
        subset=["Chill-portion error"], color="#fff3c4"), hide_index=True, use_container_width=True)
    other = ev["elevation_rule"]
    st.caption("Same check for other metrics: " + "; ".join(
        f"{q.replace('_', ' ')}: rule {'used' if r['passed'] else 'not used (a single method did as well)'}"
        for q, r in other.items() if q != "chill_portions"))

# ---------------------------------------------------------------- 4. error bars
st.header("4 · Every number comes with an error bar you can trust")
c1, c2 = st.columns([3, 2])
with c1:
    sc = err_temp["scores"]
    labels = {"table_average": "Same guess everywhere", "similar_stations": "Similar stations",
              "disagreement": "IDW vs Open-Meteo disagreement", "disagreement+geometry": "Disagreement + distance"}
    fig = go.Figure(go.Bar(
        x=[labels[k] for k in sc], y=[v["mean_miss"] for v in sc.values()],
        marker_color=["#bbbbbb", "#9db7d8", "#1baf7a", "#7fd1b2"],
        text=[f"{v['mean_miss']:.2f} °C · {v['range_coverage_pct']:.0f}% in range" for v in sc.values()],
        textposition="outside", cliponaxis=False))
    fig.update_layout(title="How far off is our predicted error? (lower is better)",
                      yaxis_title="miss of the predicted error (°C)", **CHART)
    fig.update_layout(showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    st.markdown(f"""
To say "**59 ± 3** chill portions" we must predict how wrong we'll be *before* we have a reading.

Open-Meteo gives us that signal: where plain IDW and **Open-Meteo-guided IDW disagree**,
the estimate is less certain. That model beat a one-size-fits-all guess, and its ~80% ranges
held the real error **{cov:.0f}%** of the time on unseen stations.

The orchard page also warns when an orchard sits outside what we tested
(e.g. a station closer than any we hid).
""")

# ---------------------------------------------------------------- 5. sensors
st.header("5 · When a sensor breaks, we notice — and repair it with the best tested fill")
c1, c2 = st.columns([3, 2])
with c1:
    fill = sens.get("fill_mae_C", {})
    show = {"linear": ("Straight line (old)", "#999999", "dot"),
            "idw": ("IDW estimate", "#eb6834", "dash"),
            "open_meteo_anchored": ("Open-Meteo, anchored", "#1baf7a", "solid"),
            "idw_anchored": ("IDW, anchored (used)", "#eb6834", "solid")}
    fig = go.Figure()
    for key, (label, color, dash) in show.items():
        if key in fill:
            hours = sorted(fill[key], key=int)
            fig.add_trace(go.Scatter(x=[int(h) for h in hours], y=[fill[key][h] for h in hours], name=label,
                                     mode="lines+markers", line=dict(color=color, width=3, dash=dash),
                                     marker=dict(size=9)))
    fig.update_layout(title=f"Filling hidden readings ({sens.get('fill_gaps_tested', 0):,} gaps tested)",
                      xaxis_title="hours missing", yaxis_title="error of the fill (°C)",
                      xaxis=dict(tickvals=[3, 6, 12, 24]), **CHART)
    st.plotly_chart(fig, use_container_width=True)
with c2:
    cp = sens.get("caught_pct", {})
    names = {"missing": "Dropout", "drift": "Drift (+6 °C)", "spike": "Spike", "stuck": "Stuck reading"}
    fig = go.Figure(go.Bar(y=[names[k] for k in names if k in cp], x=[cp[k] for k in names if k in cp],
                           orientation="h", marker_color="#2a78d6",
                           text=[f"{cp[k]:.0f}%" for k in names if k in cp], textposition="outside",
                           cliponaxis=False))
    fig.update_layout(title="Planted faults caught", xaxis=dict(range=[0, 110], title="% caught"),
                      **{**CHART, "height": 260}, showlegend=False)
    st.plotly_chart(fig, use_container_width=True)
    st.markdown(f"""
"Anchored" means the estimate is shifted to meet the sensor's own readings either side of the gap.
On healthy records the checks raised **{sens.get('alarms_per_station_season', 0):.2f} alarms per
station per season**. The stuck sensors we missed were only **{sens.get('stuck_error_C', {}).get('missed', 0):.1f} °C**
off (caught ones: {sens.get('stuck_error_C', {}).get('caught', 0):.1f} °C). A flat line while the neighbors
are flat too is treated as fog, not a fault.
""")

# ---------------------------------------------------------------- try it
st.header("Try it")
t1, t2, t3 = st.columns(3)
t1.page_link("app.py", label="**Your orchard** — chill so far, ± error, and which source we trust there", icon="🌳")
t2.page_link("pages/3_Sensor_health.py", label="**Break a sensor** — watch it get caught and repaired", icon="🛠️")
t3.page_link("pages/2_Source_validation.py", label="**Compare sources** region by region", icon="📍")

with st.expander("What we have not proven yet"):
    st.markdown(f"""
- **{s['stations']} stations, {len(s['seasons'])} winters, valley and coast regions.** No mountain region yet; only
  {sum(r['stations'] for r in ev['by_elevation'][2:])} stations sit 50 m or more from their neighbor's elevation.
- Hiding a station puts its neighbors 7–67 km away; orchards closer than that to a station were not tested (the app warns).
- GDD error bars are not validated: our tests cover Nov 1 – Feb 28 and GDD counts from bloom.
- The 14-day forecast was not tested separately; the history test is about past weather.
- We used no CIMIS quality flags, so "healthy" records may contain real faults.
""")
st.caption("Data: CIMIS (California DWR) · Open-Meteo (ERA5-Land; contains modified Copernicus Climate Change "
           "Service information) · chill models as used by UC Davis FNRIC.")
