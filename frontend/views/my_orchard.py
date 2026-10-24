"""My orchard: how much winter chill has my orchard had, is that normal, what's next?

Three steps for a grower: say where the orchard is, pick the crop, read the answer in plain
sentences. The detailed numbers for farm advisors sit in an expander at the bottom.
"""
from datetime import date

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from views.common import FAULTS, credits, get, km_to_mi, m_to_ft, nice_date

CROPS = {"Almonds": "almond", "Walnuts": "walnut", "Pistachios": "pistachio", "Prunes": "prune"}
CARD = ("<div style='background:{bg};border-radius:12px;padding:16px 18px;height:100%'>"
        "<div style='font-size:0.95rem;opacity:0.75'>{title}</div>"
        "<div style='font-size:1.7rem;font-weight:700;margin:4px 0'>{value}</div>"
        "<div style='font-size:0.95rem'>{note}</div></div>")


def card(col, title: str, value: str, note: str, bg: str = "#f3f6f1"):
    """A simple answer box: a question, a big answer, one line of explanation."""
    col.markdown(CARD.format(title=title, value=value, note=note, bg=bg), unsafe_allow_html=True)


def set_place(lat: float, lon: float, label: str, holdout: str | None = None):
    """Remember the chosen orchard location for the rest of the session."""
    st.session_state.pin = {"lat": lat, "lon": lon, "label": label, "holdout_station": holdout}


def vs_past_years(value: float, pct: dict) -> tuple[str, str, str]:
    """Behind / About normal / Ahead, compared with past winters on the same date."""
    if not pct:
        return "No past years", "We could not load past winters for this spot.", "#f3f6f1"
    diff = value - pct["p50"]
    if value < pct["p10"]:
        return "Behind", f"{abs(diff):.0f} portions below a typical year (usual range {pct['p10']:.0f}–{pct['p90']:.0f}).", "#fdecea"
    if value > pct["p90"]:
        return "Ahead", f"{diff:.0f} portions above a typical year (usual range {pct['p10']:.0f}–{pct['p90']:.0f}).", "#e8f5e9"
    return "About normal", f"A typical year has {pct['p50']:.0f} by this date (usual range {pct['p10']:.0f}–{pct['p90']:.0f}).", "#e8f5e9"


def how_sure(value: float, err: dict | None, within_tested: bool) -> tuple[str, str]:
    """High / Good / Rough, from the size of the give-or-take compared with the total."""
    if not err:
        return "Unknown", "We have no test results for a place like this yet."
    share = err["plus_minus"] / max(value, 1)
    level = "High" if share <= 0.06 else "Good" if share <= 0.12 else "Rough"
    if not within_tested and level == "High":
        level = "Good"
    return level, share


def source_sentence(est: dict, stations: dict) -> str:
    """Where the temperatures came from, in one plain sentence."""
    ch, inputs = est["chill"], (est.get("uncertainty") or {}).get("inputs", {})
    method = ch.get("method")
    sid = (ch.get("method_details") or {}).get("station_id") or inputs.get("nearest_station")
    name = stations.get(sid, f"station {sid}")
    km, diff_m = inputs.get("dist_km"), inputs.get("elev_diff_m")
    where = f"{km:.0f} km ({km_to_mi(km):.0f} miles) away" if km is not None else "nearby"
    if method == "open_meteo_corrected" and diff_m is not None:
        updown = "higher" if diff_m < 0 else "lower"  # elev_diff = station minus orchard
        return (f"Temperatures come from the **{name}** weather station, {where}, **adjusted for your orchard "
                f"sitting about {m_to_ft(abs(diff_m)):.0f} ft {updown}** using a weather model. On slopes like "
                f"this, that tested more accurate than the station alone.")
    if method == "nearest":
        return (f"Temperatures come from the **{name}** weather station, {where}. Your orchard is at about "
                f"the same height, so the closest real station tested most accurate here.")
    if method == "idw":
        return "Temperatures are an average of the nearest weather stations, weighted by distance."
    return "Temperatures come from the Open-Meteo weather model."


# ---------------------------------------------------------------- page
st.title("🌳 How much winter chill has my orchard had?")
st.markdown("Nut and fruit trees need enough cold hours in winter to bloom well. We count them for "
            "**your** orchard, even if the nearest weather station is far away or has closed.")

if "pin" not in st.session_state:
    st.session_state.pin = None
stations = {s["station_id"]: s["name"] for s in get("/stations")}
demo = get("/demo")

# ---------------------------------------------------------------- step 1: where
st.subheader("1 · Where is your orchard?")
with st.form("search"):
    q1, q2 = st.columns([4, 1])
    query = q1.text_input("Town or nearest place", placeholder="e.g. Chico, Durham, Arbuckle",
                          label_visibility="collapsed")
    searched = q2.form_submit_button("Search", use_container_width=True)
if searched and query.strip():
    st.session_state.matches = get("/geocode", q=query.strip())
matches = st.session_state.get("matches") or []
if matches:
    labels = [f"{m['name']}, {m.get('admin1') or ''}".strip(", ") for m in matches]
    p1, p2 = st.columns([4, 1])
    pick = p1.selectbox("Pick the right place", range(len(matches)), format_func=lambda i: labels[i],
                        label_visibility="collapsed")
    if p2.button("Use this place", use_container_width=True, type="primary"):
        set_place(matches[pick]["lat"], matches[pick]["lon"], labels[pick])
        st.session_state.matches = []
        st.rerun()
elif searched:
    st.info("We couldn't find that place in California. Try a nearby town, or pick it on the map below.")

st.markdown("Or try an example:")
e1, e2 = st.columns(2)
if e1.button("Durham — its weather station closed in 2025", use_container_width=True):
    d = demo["durham"]
    set_place(d["lat"], d["lon"], "Durham, Butte County", d.get("holdout_station"))
if e2.button("A foothill orchard near Auburn", use_container_width=True):
    d = demo["foothill"]
    set_place(d["lat"], d["lon"], "Foothill orchard near Auburn")

with st.expander("Or click your orchard on a map", expanded=False):
    m = folium.Map(location=[38.6, -121.4], zoom_start=7)
    for s in get("/stations"):
        if s.get("active", True):
            folium.CircleMarker([s["lat"], s["lon"]], radius=3, tooltip=f"{s['name']} weather station",
                                color="#2f7d4f").add_to(m)
    clicked = st_folium(m, height=380, use_container_width=True, key="map")
    if clicked and clicked.get("last_clicked"):
        ll = clicked["last_clicked"]
        pin_now = st.session_state.pin or {}
        if (round(ll["lat"], 4), round(ll["lng"], 4)) != (round(pin_now.get("lat", 0), 4), round(pin_now.get("lon", 0), 4)):
            set_place(ll["lat"], ll["lng"], f"Your spot ({ll['lat']:.3f}, {ll['lng']:.3f})")
            st.rerun()

pin = st.session_state.pin
if not pin:
    st.info("Search for your town, try an example, or click the map to see your orchard's chill.")
    credits()
    st.stop()

# ---------------------------------------------------------------- step 2: crop and date
st.subheader("2 · What do you grow?")
c1, c2, c3 = st.columns(3)
crop_label = c1.selectbox("Crop", list(CROPS))
as_of = c2.date_input("Date to check", value=date(2026, 2, 1),
                      help="Chill is counted from Nov 1. Pick any day in winter, this year or a past one.")
target = c3.number_input("My variety needs (chill portions, optional)", min_value=0, value=0, step=1,
                         help="Ask your farm advisor or nursery; it differs by variety. Leave 0 to skip.")

exclude = [pin["holdout_station"]] if pin.get("holdout_station") else []
with st.spinner("Working out your orchard's chill… a new place can take a minute the first time."):
    est = get("/estimate", lat=pin["lat"], lon=pin["lon"], as_of=str(as_of), crop=CROPS[crop_label],
              exclude=exclude, range_low_f=st.session_state.get("range_low", 32.0),
              range_high_f=st.session_state.get("range_high", 45.0))

if est["data_source"] == "synthetic":
    st.warning("Showing made-up TEST data. Set DATA_SOURCE=cimis for real numbers.")

ch, hist, proj = est["chill"], est["history"], est["projection"]
unc = est.get("uncertainty") or {}
chill_err = unc.get("chill", {}) if unc.get("available") else {}
cp, cp_err = ch["chill_portions"], chill_err.get("chill_portions")
start_txt = nice_date(ch["start"])

# ---------------------------------------------------------------- step 3: the answer
st.subheader(f"3 · {pin['label']}: the answer")
give = f" (give or take {cp_err['plus_minus']:.0f})" if cp_err else ""
st.markdown(f"<div style='font-size:1.35rem;margin:6px 0 14px'>Your orchard has had "
            f"<b>{cp:.0f} chill portions{give}</b> since <b>{start_txt}</b>, up to {nice_date(str(as_of))}.</div>",
            unsafe_allow_html=True)

pct = hist["percentiles"].get("chill_portions", {})
status, status_note, status_bg = vs_past_years(cp, pct)
a1, a2, a3 = st.columns(3)
card(a1, f"Compared with the last {len(hist['seasons'])} winters", status, status_note, status_bg)
if "added" in proj:
    how = "the weather forecast" if proj.get("method") == "forecast" else "what usually happens in the next two weeks"
    card(a2, "Next 2 weeks", f"+{proj['added']['chill_portions']:.0f} more", f"Expected, based on {how}.")
else:
    card(a2, "Next 2 weeks", "—", "No outlook available for this date.")
if target:
    left = max(target - cp, 0)
    if left == 0:
        card(a3, "Your variety's need", "Reached ✅", f"{cp:.0f} of {target} portions.", "#e8f5e9")
    else:
        soon = proj.get("added", {}).get("chill_portions", 0)
        hint = "may reach it in the next 2 weeks." if soon >= left else f"{left:.0f} portions still to go."
        card(a3, "Your variety's need", f"{cp / target * 100:.0f}% there", f"{cp:.0f} of {target} portions; {hint}")
else:
    level, share = how_sure(cp, cp_err, unc.get("within_tested_range", True))
    card(a3, "How sure are we?", level,
         f"Usually within {cp_err['plus_minus']:.0f} portions at places like yours." if cp_err
         else "No test results for a place like this yet.")

# Where the numbers come from, and whether that station is healthy.
st.markdown("")
st.markdown(source_sentence(est, stations))
alerts = est.get("sensor_alerts") or {}
if alerts.get("status") == "alert":
    name = stations.get(alerts["station_id"], f"station {alerts['station_id']}")
    what = ", ".join(sorted({FAULTS.get(e["type"], e["type"]) for e in alerts.get("episodes", [])}))
    msg = f"⚠️ The **{name}** weather station had problems for **{alerts['faulty_hours']} hours** this winter ({what})."
    if alerts.get("repaired_in_this_estimate"):
        msg += " We replaced those hours with our best estimate from nearby stations, so these numbers are still usable."
    elif alerts.get("alert_confidence") == "low":
        msg += " Its neighbors are far away, so we're not sure it was really broken and left its readings as they are."
    st.warning(msg)
if target:
    level, _ = how_sure(cp, cp_err, unc.get("within_tested_range", True))
    st.markdown(f"**How sure are we?** {level}." + (f" At places like yours we're usually within "
                f"{cp_err['plus_minus']:.0f} portions." if cp_err else ""))
if unc.get("warning"):
    st.caption("Note: your orchard is closer to a weather station than any place we tested, so the "
               "give-or-take is our best guess rather than a measured one.")
st.page_link("views/trust.py", label="Why you can trust these numbers", icon="🔍")

# ---------------------------------------------------------------- chart
days = pd.date_range(ch["start"], periods=len(ch["curve"]), freq="D")
fig = go.Figure()
for s in hist["seasons"]:
    n = min(len(s["curve"]), len(days) + 14)
    x = pd.date_range(ch["start"], periods=n, freq="D")
    fig.add_trace(go.Scatter(x=x, y=s["curve"][:n], mode="lines", line=dict(color="#cfd8cf", width=1.5),
                             name=f"Winter {s['season']}", hovertemplate="%{y:.0f} portions<extra>Winter "
                             + s["season"] + "</extra>", showlegend=False))
fig.add_trace(go.Scatter(x=days, y=ch["curve"], mode="lines", line=dict(color="#2f7d4f", width=4),
                         name="This winter", hovertemplate="%{x|%b %d}: %{y:.0f} portions<extra></extra>"))
if target:
    fig.add_hline(y=target, line_dash="dash", line_color="#b5651d",
                  annotation_text=f"your variety needs {target}", annotation_position="top left")
fig.update_layout(title="This winter (green) compared with past winters (gray)", yaxis_title="chill portions",
                  height=380, margin=dict(l=10, r=10, t=50, b=10), showlegend=False,
                  xaxis=dict(tickformat="%b %d"))
st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------- proof for the Durham example
if pin.get("holdout_station"):
    with st.expander("Check our work: we hid the real Durham station and estimated it", expanded=True):
        v = get("/validate", station_id=pin["holdout_station"])
        w = v["with_elevation_adjustment"]
        b1, b2, b3 = st.columns(3)
        b1.metric("What the station really measured", f"{w['actual']['chill_portions']:.0f} portions")
        b2.metric("What we estimated without it", f"{w['estimated']['chill_portions']:.0f} portions")
        b3.metric("Difference", f"{abs(w['error']['chill_portions']):.0f} portions")
        st.caption(f"Winter {nice_date(v['season'][:10])} to {nice_date(v['season'][-10:])}.")

# ---------------------------------------------------------------- details for advisors
with st.expander("More numbers (for farm advisors)"):
    def pm(metric):
        e = chill_err.get(metric)
        return f" ± {e['plus_minus']:.0f}" if e else ""
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Chill hours (32–45 °F)", f"{ch['chill_hours']:.0f}{pm('chill_hours')}")
    d2.metric("Hours at or below 45 °F", f"{ch['hours_lt_45']:.0f}")
    d3.metric("Utah chill units", f"{ch['utah_units']:.0f}{pm('utah_units')}", help="Warm hours take units away")
    g = est["gdd"]
    d4.metric("Heat since bloom (GDD, °C·days)", f"{g['total_c_days']:.0f}" if g["started"] else "Not started",
              help=f"Counted from {nice_date(g['start'])} (assumed bloom date). Its error is not tested yet.")
    st.caption(f"All chill numbers counted from {start_txt}. Chill portions use the Dynamic Model, which "
               f"suits California's mild winters. {ch['hours_missing']} hours had no data.")
    r1, r2 = st.columns(2)
    r1.number_input("Count hours from (°F)", value=32.0, step=1.0, key="range_low")
    r2.number_input("…up to (°F)", value=45.0, step=1.0, key="range_high")
    cr = ch["custom_range"]
    st.markdown(f"Since {start_txt}: **{cr['hours_below']:.0f}** hours at or below {cr['low_f']:.0f} °F · "
                f"**{cr['hours_between']:.0f}** hours between {cr['low_f']:.0f} and {cr['high_f']:.0f} °F · "
                f"**{cr['hours_above']:.0f}** hours above {cr['high_f']:.0f} °F")

credits()
