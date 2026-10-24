"""Sensor health: fill broken stretches of a station's record (and, later, detect them).

When a station we otherwise trust stops reporting or reports nonsense for a few hours,
we can fill those hours from what we know:
  linear        straight line between the station's own readings either side of the gap
  <method>      the hour-by-hour estimate from source_compare (nearest, idw, open_meteo, ...)
  <method>_anchored
                that estimate shifted to meet the station's own readings at both edges of the
                gap: the shape of the day comes from the estimate, the level from the station.

scripts/sensor_fill_experiment.py hides real readings and measures which fill is closest.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def gap_edges(sensor: pd.Series, gap: pd.DatetimeIndex) -> tuple[float | None, float | None]:
    """The station's last reading before the gap and first reading after it (None if missing)."""
    before = sensor.get(gap[0] - pd.Timedelta(hours=1))
    after = sensor.get(gap[-1] + pd.Timedelta(hours=1))
    ok = lambda v: v is not None and np.isfinite(v)  # noqa: E731
    return (float(before) if ok(before) else None, float(after) if ok(after) else None)


def linear_fill(sensor: pd.Series, gap: pd.DatetimeIndex) -> pd.Series:
    """Straight line from the reading before the gap to the reading after it."""
    before, after = gap_edges(sensor, gap)
    if before is None or after is None:
        return pd.Series(np.nan, index=gap)
    steps = np.arange(1, len(gap) + 1) / (len(gap) + 1)
    return pd.Series(before + (after - before) * steps, index=gap)


def estimate_fill(sensor: pd.Series, estimate: pd.Series, gap: pd.DatetimeIndex,
                  anchored: bool) -> pd.Series:
    """Fill the gap from an estimate; if anchored, shift it to meet the station at both edges.

    The shift at each edge is (station reading - estimate) one hour outside the gap. Across
    the gap the shift moves in a straight line from one edge value to the other. With only one
    edge available, that edge's shift is used throughout.
    """
    est = estimate.reindex(gap)
    if not anchored:
        return est
    before, after = gap_edges(sensor, gap)
    e_before = estimate.get(gap[0] - pd.Timedelta(hours=1))
    e_after = estimate.get(gap[-1] + pd.Timedelta(hours=1))
    off_b = before - e_before if before is not None and e_before is not None and np.isfinite(e_before) else None
    off_a = after - e_after if after is not None and e_after is not None and np.isfinite(e_after) else None
    if off_b is None and off_a is None:
        return est  # nothing to anchor to: plain estimate
    off_b = off_a if off_b is None else off_b
    off_a = off_b if off_a is None else off_a
    steps = np.arange(1, len(gap) + 1) / (len(gap) + 1)
    return est + (off_b + (off_a - off_b) * steps)


def fill_options(sensor: pd.Series, estimates: dict[str, pd.Series],
                 gap: pd.DatetimeIndex) -> dict[str, pd.Series]:
    """Every fill we know, for one gap: linear, and each estimate raw and anchored."""
    out = {"linear": linear_fill(sensor, gap)}
    for name, est in estimates.items():
        if est is None or est.dropna().empty:
            continue
        out[name] = estimate_fill(sensor, est, gap, anchored=False)
        out[f"{name}_anchored"] = estimate_fill(sensor, est, gap, anchored=True)
    return out


# ---------------------------------------------------------------- detection

def _runs(mask: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Consecutive True stretches in an hourly mask: (first hour, last hour, length)."""
    out = []
    groups = (mask != mask.shift()).cumsum()
    for _, block in mask[mask].groupby(groups[mask]):
        out.append((block.index[0], block.index[-1], len(block)))
    return out


def detect_faults(sensor: pd.Series, reference: pd.Series, is_filled: pd.Series | None = None) -> dict:
    """Find stretches where a station's readings look broken, and say why.

    sensor: the station's hourly °F (NaN = no reading).
    reference: an estimate of the same hours from neighbors (never uses this station).
    is_filled: hours data.fill_gaps already filled (short gaps); they are not real readings.
    Returns {"faulty": hourly True/False mask, "episodes": [...], "notes": [...]}.
    A long flat stretch is only called 'stuck' if the reference moved meanwhile; if the
    reference was flat too (fog, still overcast night) it is noted, not flagged.
    """
    idx = sensor.index.union(reference.index)
    full = pd.date_range(idx.min(), idx.max(), freq="h") if len(idx) else idx
    s = sensor.reindex(full)
    ref = reference.reindex(full)
    real = s.copy()
    if is_filled is not None:
        real[is_filled.reindex(full).fillna(False).astype(bool)] = np.nan
    faulty = pd.Series(False, index=full)
    episodes, notes = [], []

    def add(kind, start, end, hours, detail):
        faulty.loc[start:end] = True
        episodes.append({"type": kind, "start": str(start), "end": str(end), "hours": int(hours),
                         "detail": detail})

    # 1. no reading for longer than we are allowed to interpolate
    for a, b, n in _runs(s.isna()):
        if n > config.MAX_FILL_HOURS:
            add("missing", a, b, n, f"no readings for {n} hours")

    # 2. values no California air temperature takes
    lo, hi = config.SENSOR_RANGE_F
    for a, b, n in _runs((s < lo) | (s > hi)):
        add("impossible_value", a, b, n, f"readings outside {lo:g}..{hi:g} °F")

    # 3. stuck: identical real readings for hours while the reference moves
    same = real.diff().eq(0)
    for a, b, n in _runs(same):
        a = a - pd.Timedelta(hours=1)  # the run includes the hour before the first repeat
        hours = n + 1
        if hours < config.SENSOR_STUCK_HOURS:
            continue
        ref_range_c = float((ref.loc[a:b].max() - ref.loc[a:b].min()) * 5 / 9)
        if np.isfinite(ref_range_c) and ref_range_c >= config.SENSOR_STUCK_REF_RANGE_C:
            add("stuck", a, b, hours, f"same reading ({real.loc[a]:.1f} °F) for {hours} hours while "
                f"neighbors changed {ref_range_c:.1f} °C")
        else:
            notes.append(f"{hours} flat hours from {a}: neighbors were flat too (fog?), not flagged")

    # 4. one-hour spikes the neighbors don't show
    jump = s.diff().abs()
    ref_jump = ref.diff().abs()
    for a, b, n in _runs((jump >= config.SENSOR_SPIKE_F) & (ref_jump < config.SENSOR_SPIKE_F / 3)):
        add("spike", a, b, n, f"jumped {jump.loc[a]:.1f} °F in an hour; neighbors moved {ref_jump.loc[a]:.1f} °F")

    # 5. drift: the station's difference from its neighbors changes for a day or more.
    #    Its usual difference (median of the past week) is removed first, so a station that is
    #    always a bit colder than its neighbors (a frost pocket, the coast) is not flagged.
    resid_c = (real - ref) * 5 / 9
    usual = resid_c.rolling(24 * config.SENSOR_BASELINE_DAYS, min_periods=48).median().shift(1)
    w = config.SENSOR_DEVIATION_HOURS
    change_c = (resid_c - usual).abs().rolling(w, min_periods=w // 2).mean()
    for a, b, n in _runs(change_c > config.SENSOR_DEVIATION_C):
        a = a - pd.Timedelta(hours=w - 1)
        add("drift", a, b, n + w - 1,
            f"{w}-hour average {change_c.loc[a:b].max():.1f} °C away from its usual difference "
            f"to neighbors (limit {config.SENSOR_DEVIATION_C:g} °C)")

    return {"faulty": faulty, "episodes": _merge(episodes), "notes": notes}


def _merge(episodes: list[dict]) -> list[dict]:
    """Join episodes of the same type that overlap or touch (e.g. one long drift seen twice)."""
    merged = []
    for e in sorted(episodes, key=lambda e: (e["type"], e["start"])):
        last = merged[-1] if merged else None
        if (last and last["type"] == e["type"]
                and pd.Timestamp(e["start"]) <= pd.Timestamp(last["end"]) + pd.Timedelta(hours=1)):
            end = max(pd.Timestamp(last["end"]), pd.Timestamp(e["end"]))
            last["end"] = str(end)
            last["hours"] = int((end - pd.Timestamp(last["start"])) / pd.Timedelta(hours=1)) + 1
        else:
            merged.append(dict(e))
    return sorted(merged, key=lambda e: e["start"])


FAULT_KINDS = ("stuck", "drift", "spike", "missing")


def plant_fault(sensor: pd.Series, kind: str, start: pd.Timestamp, hours: int) -> tuple[pd.Series, pd.DatetimeIndex]:
    """Copy of a record with one made-up fault from `start` (for tests and the demo).

    stuck: the reading at start repeats; drift: +6 °C; spike: +20 °F; missing: no readings.
    """
    span = pd.date_range(start, periods=hours, freq="h")
    broken = sensor.copy()
    span = span[span.isin(broken.index)]
    if kind == "stuck":
        broken.loc[span] = sensor.loc[span[0]]
    elif kind == "drift":
        broken.loc[span] = sensor.loc[span] + 6 * 9 / 5
    elif kind == "spike":
        broken.loc[span] = sensor.loc[span] + 20
    elif kind == "missing":
        broken.loc[span] = np.nan
    else:
        raise ValueError(f"unknown fault {kind}; use one of {FAULT_KINDS}")
    return broken, span


# ---------------------------------------------------------------- repair

# Order to try, from the fill experiment (data/validation/fill_experiment.csv): anchored IDW
# was closest at every gap length (3-24 h); anchored Open-Meteo is the fallback when there are
# no neighbors (still far better than a straight line past 3 h); linear is the last resort.
REPAIR_ORDER = ("idw_anchored", "open_meteo_anchored", "linear")


def repair(sensor: pd.Series, faulty: pd.Series, estimates: dict[str, pd.Series]) -> tuple[pd.Series, pd.Series]:
    """Replace faulty hours with the best available fill; returns (series, source per hour).

    Each faulty stretch is filled as one gap, so anchoring uses the good readings either side.
    source is "sensor" for kept readings, the fill name for replaced ones, NaN if unfillable.
    """
    full = sensor.reindex(faulty.index)
    clean = full.where(~faulty)
    out, source = clean.copy(), pd.Series(np.where(clean.notna(), "sensor", None), index=full.index, dtype=object)
    raw_estimates = {"idw": estimates.get("idw"), "open_meteo": estimates.get("open_meteo")}
    for a, b, _ in _runs(faulty | clean.isna()):
        gap = pd.date_range(a, b, freq="h")
        options = fill_options(clean, raw_estimates, gap)
        for name in REPAIR_ORDER:
            fill = options.get(name)
            if fill is not None and fill.notna().all():
                out.loc[gap] = fill.to_numpy()
                source.loc[gap] = name
                break
    return out, source
