"""Can I trust these numbers? The evidence behind every number, in plain words.

Everything comes from GET /evidence: real CIMIS stations we hid and estimated, the hills rule,
the give-or-take, and the broken-sensor tests. Technical detail is in an expander at the end.
"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from views.common import CHART, METHODS, c_to_f, credits, get

ev = get("/evidence")
if not ev.get("available"):
    st.error("The test results are not built yet (data/validation/loso_table.csv).")
    st.stop()
s, rule_cp, sens = ev["summary"], ev["elevation_rule"]["chill_portions"], ev["sensor"]
err_temp = ev["error_models"]["hourly_temp"]
cov = err_temp["scores"][err_temp["best_model"]]["range_coverage_pct"]
better = (1 - rule_cp["elevation_rule_mean_error"] / rule_cp["always_method_mean_error"]["idw"]) * 100


def bars(values: dict, title: str, unit: str) -> go.Figure:
    """One bar per way of getting temperatures; shortest bar = most accurate."""
    order = sorted(values, key=values.get, reverse=True)
    best = min(values, key=values.get)
    fig = go.Figure(go.Bar(
        y=[METHODS[m]["label"] for m in order], x=[values[m] for m in order], orientation="h",
        marker_color=[METHODS[m]["color"] for m in order],
        text=[f"off by {values[m]:.1f}" + ("  ← most accurate" if m == best else "") for m in order],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: off by %{x:.1f} " + unit + " on average<extra></extra>"))
    fig.update_layout(title=title, xaxis_title=f"how far off, on average ({unit})", **CHART)
    fig.update_layout(height=290, showlegend=False, margin=dict(l=10, r=120, t=50, b=10))
    return fig


# ---------------------------------------------------------------- short answer
st.title("🔍 Can I trust these numbers?")
st.success(
    f"**Yes, within the stated give-or-take.** We tested the app on **{s['stations']} real weather stations**: "
    f"we hid each one, worked out its chill the way we would for your orchard, and checked how close we got. "
    f"That's how we chose where the temperatures come from, and how we know the ± on every number.")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Real stations tested", f"{s['stations']}", help=f"{s['cases']} tests over {len(s['seasons'])} winters")
k2.metric("Closer than the usual method", f"{better:.0f}%", help="Chill count error, versus averaging nearby stations")
k3.metric("Our ± was right", f"{cov / 20:.0f} times in 5", help=f"{cov:.0f}% of the time on stations we did not tune on")
caught = sens.get("caught_pct", {})
k4.metric("Broken stations spotted", f"{sum(caught.values()) / max(len(caught), 1):.0f}% of the time",
          help="Average over four kinds of faults we planted on purpose")

# ---------------------------------------------------------------- how we tested
st.header("How we tested it")
c1, c2 = st.columns([3, 2])
with c1:
    df = pd.DataFrame(ev["stations"])
    df["best"] = df[[f"chill_portions_error_{m}" for m in METHODS]].abs().idxmin(axis=1).str.replace(
        "chill_portions_error_", "", regex=False)
    first = df.drop_duplicates("station_id")
    fig = go.Figure()
    for key, m in METHODS.items():
        sub = first[first["best"] == key]
        if not sub.empty:
            fig.add_trace(go.Scattermapbox(
                lat=sub["lat"], lon=sub["lon"], mode="markers", name=m["label"],
                marker=dict(size=13, color=m["color"]), customdata=sub[["name"]],
                hovertemplate="<b>%{customdata[0]}</b><extra>" + m["label"] + " worked best</extra>"))
    fig.update_layout(mapbox=dict(style="open-street-map", center=dict(lat=37.6, lon=-121.0), zoom=5.3),
                      title="The stations we tested, colored by what worked best there", **{**CHART, "height": 460})
    st.plotly_chart(fig, use_container_width=True)
with c2:
    st.markdown(f"""
Think of it as a test with the answers hidden.

1. **Hide** a real weather station, as if it had closed — just like the **Durham** station did in 2025.
2. **Work out its chill** using only the other stations and a weather model, the same way we do for your orchard.
3. **Compare** with what the station really measured.
4. **Repeat** for all {s['stations']} stations, over {len(s['seasons'])} winters, in the Sacramento Valley,
   San Joaquin Valley and Central Coast.

We tried four ways of getting temperatures for a spot:
- **{METHODS['nearest']['label']}**
- **{METHODS['idw']['label']}**
- **{METHODS['open_meteo']['label']}** (a computer weather model)
- **{METHODS['open_meteo_corrected']['label']}** (the station, adjusted for the height difference)
""")

# ---------------------------------------------------------------- why not just...
st.header("Why not just use the closest station, or a weather model?")
me = ev["method_errors"]
c1, c2 = st.columns(2)
c1.plotly_chart(bars(me["Chill portions"], "Counting chill: how far off each way was", "portions"),
                use_container_width=True)
temp_f = {k: c_to_f(v) for k, v in me["Hourly temperature (°C)"].items()}
c2.plotly_chart(bars(temp_f, "Hour-by-hour temperature: how far off", "°F"), use_container_width=True)
st.markdown(
    "- **A weather model alone is the least accurate.** In the valleys it runs too warm in winter fog, "
    "so it would undercount your chill.\n"
    "- **Averaging several stations gets the temperature close, but miscounts chill.** Averaging smooths "
    "out the coldest night hours — exactly the hours that count.\n"
    "- **A real nearby station keeps those cold hours**, so on flat ground it counts chill best.")

# ---------------------------------------------------------------- hills
st.header("What if my orchard is up a slope or down in a hollow?")
bins_ft = {"0-25 m": "within 80 ft", "25-50 m": "80–160 ft", "50-100 m": "160–330 ft", "100+ m": "more than 330 ft"}
fig = go.Figure()
for key, m in METHODS.items():
    fig.add_trace(go.Bar(
        x=[f"{bins_ft.get(r['elevation_difference'], r['elevation_difference'])}<br>({r['stations']} stations)"
           for r in ev["by_elevation"]],
        y=[r["chill_portions"][key] for r in ev["by_elevation"]], name=m["label"], marker_color=m["color"],
        marker_line=dict(width=2, color="white"),
        hovertemplate=m["label"] + "<br>%{x}<br>off by %{y:.1f} portions<extra></extra>"))
fig.add_vline(x=1.5, line_dash="dash", line_color="#888")
fig.add_annotation(x=1.5, y=1.03, yref="paper", text="we switch at about 160 ft", showarrow=False)
fig.update_layout(barmode="group", title="Chill count error by height difference between orchard and station",
                  xaxis_title="height difference to the closest station",
                  yaxis_title="how far off (portions)", **{**CHART, "height": 420})
st.plotly_chart(fig, use_container_width=True)
c1, c2 = st.columns(2)
c1.info("**Orchard within about 160 ft (50 m) of the station's height:** we use the closest station as it is. "
        "Neighbors at the same height share the same cold air.")
c2.info("**Orchard higher or lower than that:** we use the closest station **adjusted with a weather model** "
        "for the height difference. Cold air pools in low spots on winter nights, and the model follows that "
        "better than a simple rule of thumb.")
st.caption(f"We did not pick 160 ft — the test did. When we left each station out and asked the data again, "
           f"it chose the same switch point {rule_cp['rule_stability']} times. This rule counted chill "
           f"{better:.0f}% more accurately than averaging nearby stations.")

# ---------------------------------------------------------------- give or take
st.header("What does the “give or take” mean?")
c1, c2 = st.columns([2, 3])
c1.metric("How often the real number fell inside our range", f"{cov:.0f}%", help="Our target is 80%")
c2.markdown(
    "When we say **55 ± 3 chill portions**, the real number is most likely between 52 and 58. "
    f"We checked this on stations we never used to set it: the real value landed inside our range "
    f"**{cov:.0f}% of the time** — about 4 times in 5, as intended.\n\n"
    "The ± is bigger where we are less sure, for example where nearby stations and the weather model "
    "disagree about your spot.")

# ---------------------------------------------------------------- broken stations
st.header("What if a weather station breaks?")
c1, c2 = st.columns([3, 2])
names = {"missing": "Stopped sending", "drift": "Slowly drifting off", "spike": "Sudden jump",
         "stuck": "Stuck on one number"}
fig = go.Figure(go.Bar(y=[names[k] for k in names if k in caught], x=[caught[k] for k in names if k in caught],
                       orientation="h", marker_color="#2f7d4f", text=[f"{caught[k]:.0f}% caught" for k in names if k in caught],
                       textposition="outside", cliponaxis=False))
fig.update_layout(title="We broke stations on purpose. How often did we notice?", xaxis=dict(range=[0, 115]),
                  **{**CHART, "height": 280}, showlegend=False)
c1.plotly_chart(fig, use_container_width=True)
fill = sens.get("fill_mae_C", {}).get("idw_anchored", {})
line = sens.get("fill_mae_C", {}).get("linear", {})
c2.markdown(f"""
Every hour we compare each station with its neighbors. When one breaks, we **warn you** and **fill the
broken hours** with our best estimate from nearby stations.

For a 6-hour gap our fill was off by about **{c_to_f(fill.get('6', 0)):.1f} °F**, versus
**{c_to_f(line.get('6', 0)):.1f} °F** for just drawing a straight line. A flat reading on a still, foggy night
is normal, so we only call a station "stuck" when its neighbors were changing.
""")
st.page_link("views/station_check.py", label="Try it: break a station on purpose and watch us catch it", icon="🛠️")

# ---------------------------------------------------------------- limits
with st.expander("What we have not proven yet"):
    st.markdown(f"""
- We tested {s['stations']} stations over {len(s['seasons'])} winters in valleys and on the coast — **no mountain areas yet**.
  Only {sum(r['stations'] for r in ev['by_elevation'][2:])} test stations were more than 160 ft above or below their neighbor.
- When we hide a station its neighbors are 4–40 miles away. **Orchards closer to a station than that were not tested**;
  the app tells you when that's the case.
- "Heat since bloom" (GDD) has no tested give-or-take yet.
- The 2-week outlook was not tested on its own.
""")

# ---------------------------------------------------------------- for judges and advisors
with st.expander("For technical readers"):
    st.markdown(f"""
**Method.** Leave-one-station-out (LOSO) validation over {s['cases']} station-seasons ({', '.join(s['regions'])};
winters ending {', '.join(s['seasons'])}). Methods: nearest station; inverse-distance weighting (IDW) with a
6.5 °C/km lapse rate; Open-Meteo {s['open_meteo_model']} at the station's coordinates; and nearest station +
(Open-Meteo at target − Open-Meteo at nearest), hour by hour. Every choice (method, elevation threshold,
error model) was scored with nested LOSO: each station judged by a rule learned without it.

**Elevation rule (chill portions):** `{rule_cp['rule_learned_most_often']}`, learned {rule_cp['rule_stability']};
mean |error| {rule_cp['elevation_rule_mean_error']:.2f} vs {rule_cp['always_method_mean_error']['nearest']:.2f}
(always nearest) and {rule_cp['always_method_mean_error']['idw']:.2f} (always IDW).
""")
    sc = err_temp["scores"]
    labels = {"table_average": "Same guess everywhere", "similar_stations": "Similar stations (distance, height)",
              "disagreement": "IDW vs Open-Meteo-guided IDW disagreement", "disagreement+geometry": "Disagreement + geometry"}
    st.dataframe(pd.DataFrame([{"Error model": labels[k], "Mean miss of predicted MAE (°C)": v["mean_miss"],
                                "Correlation": v["correlation"], "80% range coverage (%)": v["range_coverage_pct"]}
                               for k, v in sc.items()]), hide_index=True, use_container_width=True)
    fm = sens.get("fill_mae_C", {})
    if fm:
        st.markdown(f"**Gap fill experiment** ({sens.get('fill_gaps_tested', 0):,} hidden gaps): MAE (°C) by gap length.")
        st.dataframe(pd.DataFrame(fm).T.round(2).sort_values("24"), use_container_width=True)
    st.markdown(f"**Fault detection:** {sens.get('alarms_per_station_season', 0):.2f} alarms per healthy station-season; "
                f"missed stuck sensors were {sens.get('stuck_error_C', {}).get('missed', 0):.2f} °C off on average.")

credits()
