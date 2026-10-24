"""Source Selection Engine: which temperature source should we trust at this orchard?

We don't guess with fixed rules. We look up what actually happened in our
leave-one-station-out tests (config.VALIDATION_TABLE) at stations that looked like
this orchard: a similar distance to the nearest station and a similar elevation
difference. Whichever method had the lowest error there is chosen, and its error
there is what we tell the grower to expect.

evaluate_selector() checks that this choosing actually helps, on stations it never saw.
recommend() only lets it switch methods if it passed that check; otherwise it uses the
single method that did best, and says why.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from . import config
from .source_compare import METHODS

# How we measure "similar": 20 km farther counts the same as 100 m more elevation difference.
DIST_SCALE_KM = 20.0
ELEV_SCALE_M = 100.0
# If even the k-th most similar test case is farther than this (in the scaled units above),
# we have no comparable evidence and say so.
MAX_SIMILARITY_GAP = 1.5


def load_table() -> pd.DataFrame:
    """The leave-one-station-out results, one row per station-season. Empty if not built yet."""
    if not config.VALIDATION_TABLE.exists():
        return pd.DataFrame()
    table = pd.read_csv(config.VALIDATION_TABLE, dtype={"station_id": str, "nearest_station_id": str})
    return table.dropna(subset=["nearest_dist_km", "nearest_elev_diff_m"])


def _gap(table: pd.DataFrame, dist_km: float, elev_diff_m: float) -> pd.Series:
    """How unlike this orchard each test case is (0 = same distance and elevation difference)."""
    d = (table["nearest_dist_km"] - dist_km) / DIST_SCALE_KM
    e = (table["nearest_elev_diff_m"].abs() - abs(elev_diff_m)) / ELEV_SCALE_M
    return np.sqrt(d ** 2 + e ** 2)


def overall_best(table: pd.DataFrame) -> str:
    """Method with the lowest average MAE over the whole table (the fallback choice)."""
    means = {m: table[f"mae_C_{m}"].mean() for m in METHODS}
    return min(means, key=means.get)


def select_source(table: pd.DataFrame, dist_km: float, elev_diff_m: float,
                  k: int | None = None, min_margin_c: float | None = None) -> dict:
    """Pick the method with the lowest error at the k most similar test cases.

    dist_km: distance from the orchard to the nearest station with data.
    elev_diff_m: that station's elevation minus the orchard's (sign ignored for now).
    We start from the method that is best on average over the whole table, and only switch
    when another method beat it by at least min_margin_c at the similar stations (small
    differences between a handful of stations are mostly noise).
    Returns the choice, the expected error per method (mean, best and worst case among the
    similar stations), and status "validated" or "insufficient_validation".
    """
    k = k or config.SELECT_K
    margin_needed = config.SELECT_MIN_MARGIN_C if min_margin_c is None else min_margin_c
    if table.empty:
        return {"selected_method": "idw", "status": "no_validation_table",
                "reason": "Validation table not built yet; using IDW as a default."}

    similar = table.assign(gap=_gap(table, dist_km, elev_diff_m)).nsmallest(k, "gap")
    expected = {}
    for m in METHODS:
        vals = similar[f"mae_C_{m}"].dropna()
        if vals.empty:
            continue
        expected[m] = {"expected_mae_C": round(float(vals.mean()), 2),
                       "best_case_mae_C": round(float(vals.min()), 2),
                       "worst_case_mae_C": round(float(vals.max()), 2),
                       "chill_portions_abs_error": round(float(similar[f"chill_portions_error_{m}"].abs().mean()), 1),
                       "n_cases": int(len(vals))}

    enough = len(similar) >= k and similar["gap"].max() <= MAX_SIMILARITY_GAP
    default = overall_best(table)
    if enough:
        local_best = min(expected, key=lambda m: expected[m]["expected_mae_C"])
        margin = expected[default]["expected_mae_C"] - expected[local_best]["expected_mae_C"]
        status = "validated"
        if local_best != default and margin >= margin_needed:
            chosen = local_best
            reason = (f"{local_best} beat the usual best method ({default}) by {margin:.2f} °C "
                      f"at the {len(similar)} most similar test stations.")
        else:
            chosen = default
            reason = (f"{default} is best on average, and no method beat it by "
                      f"{margin_needed:.2f} °C or more at the {len(similar)} most similar test stations.")
    else:
        chosen = overall_best(table)
        status = "insufficient_validation"
        reason = ("No test stations are similar enough to this orchard; "
                  f"using the method that is best on average ({chosen}).")

    return {
        "selected_method": chosen,
        "status": status,
        "reason": reason,
        "expected_error": expected,
        "similar_cases": similar[["station_id", "name", "season_end", "nearest_dist_km",
                                  "nearest_elev_diff_m", "best_method_by_mae"]]
                         .round(1).to_dict("records"),
        "similarity_gap_max": round(float(similar["gap"].max()), 2),
    }


@lru_cache(maxsize=4)
def _evaluation_for(path: str, mtime: float) -> dict:
    """evaluate_selector on the saved table, recomputed only when the file changes."""
    return evaluate_selector(load_table())


def recommend(dist_km: float, elev_diff_m: float) -> dict:
    """The source to use at an orchard, gated by evidence.

    Per-orchard choosing is used only if it beat the best single method on unseen stations
    (evaluate_selector). Otherwise every orchard gets that single method. Either way the
    expected error comes from the most similar test stations.
    """
    table = load_table()
    choice = select_source(table, dist_km, elev_diff_m)
    if table.empty:
        return choice
    ev = _evaluation_for(str(config.VALIDATION_TABLE), config.VALIDATION_TABLE.stat().st_mtime)
    best_fixed, gain = ev["best_fixed_method"], ev["selector_gain_vs_best_fixed_C"]
    choice["selector_check"] = {
        "selector_mean_mae_C": ev["strategies"]["selector"]["mean_mae_C"],
        f"always_{best_fixed}_mean_mae_C": ev["strategies"][f"always_{best_fixed}"]["mean_mae_C"],
        "cases": ev["cases"], "passed": gain > 0,
    }
    if gain <= 0 and choice["selected_method"] != best_fixed:
        choice["selected_method"] = best_fixed
        choice["reason"] = (f"Choosing per orchard did not beat always using {best_fixed} on stations "
                            f"it never saw ({ev['cases']} cases), so we use {best_fixed}.")
    elif gain <= 0:
        choice["reason"] = (f"{best_fixed} had the lowest error on stations it never saw "
                            f"({ev['cases']} cases); per-orchard switching did not improve on it.")
    return choice


# ---------------------------------------------------------------- how wrong will IDW be here?
# Four ways to predict how wrong a method will be at an orchard, before we have any reading
# there. evaluate_error_models() scores them on unseen stations, separately for each quantity
# (hourly temperature, chill portions, chill hours, Utah units); the winner is used.

# table_average is the reference: the same guess everywhere. A model must beat it to be useful.
ERROR_MODELS = ("table_average", "similar_stations", "disagreement", "disagreement+geometry")
# Every table row covers Nov 1 - Feb 28 (120 days); season-total errors are for that window.
TABLE_WINDOW_HOURS = 120 * 24
INTERVAL_Q = (0.1, 0.9)  # the range we report should hold the real error about 80% of the time
# quantity -> (table column pattern, unit). Season totals use the size of the error (abs).
QUANTITIES = {
    "hourly_temp": ("mae_C_{m}", "°C"),
    "chill_portions": ("chill_portions_error_{m}", "portions"),
    "chill_hours": ("chill_hours_error_{m}", "hours"),
    "utah_units": ("utah_units_error_{m}", "units"),
}


def _target(table: pd.DataFrame, quantity: str, method: str) -> pd.Series:
    """The error we want to predict, one value per test case (always >= 0)."""
    return table[QUANTITIES[quantity][0].format(m=method)].abs()


def _features(df: pd.DataFrame, model: str) -> np.ndarray:
    """Design matrix (with a constant column) for the linear error models."""
    cols = [np.ones(len(df)), df["idw_disagreement_C"].to_numpy(float)]
    if model == "disagreement+geometry":
        cols += [df["nearest_dist_km"].to_numpy(float) / DIST_SCALE_KM,
                 df["nearest_elev_diff_m"].abs().to_numpy(float) / ELEV_SCALE_M]
    return np.column_stack(cols)


def predict_error(table: pd.DataFrame, model: str, dist_km: float, elev_diff_m: float,
                  disagreement: float | None, method: str = "idw", k: int | None = None,
                  quantity: str = "hourly_temp") -> dict | None:
    """Expected error of `method` at one orchard for one quantity, plus an ~80% range.

    table_average: the average error over all test cases; range = their 10th-90th percentile.
    similar_stations: average over the k test stations most like this orchard (distance,
        elevation difference); range = their 10th-90th percentile.
    disagreement(+geometry): straight-line fit of the error on the IDW-vs-Open-Meteo gap (and
        distance, elevation difference); range = prediction + the fit's 10th-90th percentile miss.
    Returns None if the model can't be used (e.g. no disagreement value, column missing).
    """
    col = QUANTITIES[quantity][0].format(m=method)
    if col not in table:
        return None
    table = table.assign(_y=_target(table, quantity, method))
    if model in ("table_average", "similar_stations"):
        pool = (table if model == "table_average" else
                table.assign(gap=_gap(table, dist_km, elev_diff_m)).nsmallest(k or config.SELECT_K, "gap"))
        vals = pool["_y"].dropna()
        if vals.empty:
            return None
        mean, lo, hi = vals.mean(), vals.quantile(INTERVAL_Q[0]), vals.quantile(INTERVAL_Q[1])
    else:
        if disagreement is None or "idw_disagreement_C" not in table:
            return None
        train = table.dropna(subset=["idw_disagreement_C", "_y"])
        if len(train) < 10:
            return None
        X, y = _features(train, model), train["_y"].to_numpy(float)
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        row = pd.DataFrame({"idw_disagreement_C": [disagreement], "nearest_dist_km": [dist_km],
                            "nearest_elev_diff_m": [elev_diff_m]})
        mean = float((_features(row, model) @ coef)[0])
        lo, hi = mean + np.quantile(resid, INTERVAL_Q[0]), mean + np.quantile(resid, INTERVAL_Q[1])
    digits = 2 if quantity == "hourly_temp" else 1
    return {"expected": round(float(max(mean, 0.0)), digits),
            "range": [round(float(max(lo, 0.0)), digits), round(float(hi), digits)],
            "unit": QUANTITIES[quantity][1], "model": model}


def evaluate_error_models(table: pd.DataFrame, method: str = "idw", k: int | None = None,
                          quantity: str = "hourly_temp") -> dict:
    """Which error model best predicts how wrong `method` will be at a station it never saw?

    Nested leave-one-station-out: each station's error is predicted from the other stations only.
    Scores: average miss of the predicted error (lower is better), correlation with the real
    error, and how often the real error fell inside the reported range (aim: about 80%).
    """
    col = QUANTITIES[quantity][0].format(m=method)
    if table.empty or "idw_disagreement_C" not in table or col not in table:
        return {"status": "no_validation_table"}
    rows = []
    for sid, test in table.groupby("station_id"):
        train = table[table["station_id"] != sid]
        for _, r in test.iterrows():
            actual = abs(r[col])
            out = {"station_id": sid, "season_end": r["season_end"], "actual": actual}
            gap = r["idw_disagreement_C"] if pd.notna(r["idw_disagreement_C"]) else None
            for model in ERROR_MODELS:
                p = predict_error(train, model, r["nearest_dist_km"], r["nearest_elev_diff_m"],
                                  gap, method, k, quantity)
                out[model] = p["expected"] if p else np.nan
                out[f"{model}_in_range"] = p["range"][0] <= actual <= p["range"][1] if p else np.nan
                out[f"{model}_width"] = p["range"][1] - p["range"][0] if p else np.nan
            rows.append(out)
    p = pd.DataFrame(rows).dropna(subset=list(ERROR_MODELS) + ["actual"])
    scores = {}
    for model in ERROR_MODELS:
        scores[model] = {
            "mean_miss": round(float((p[model] - p["actual"]).abs().mean()), 3),
            # undefined when either side never varies (e.g. the same guess everywhere)
            "correlation": (round(float(np.corrcoef(p[model], p["actual"])[0, 1]), 2)
                            if p[model].std() > 0 and p["actual"].std() > 0 else None),
            "range_coverage_pct": round(float(p[f"{model}_in_range"].astype(float).mean() * 100), 1),
            "mean_range_width": round(float(p[f"{model}_width"].mean()), 2),
        }
    best = min(ERROR_MODELS, key=lambda m: scores[m]["mean_miss"])
    return {"quantity": quantity, "unit": QUANTITIES[quantity][1], "method": method,
            "cases": len(p), "stations": int(p["station_id"].nunique()),
            "scores": scores, "best_model": best,
            "gain_vs_table_average": round(scores["table_average"]["mean_miss"] - scores[best]["mean_miss"], 3),
            "note": "Each station's error is predicted from the other stations only."}


@lru_cache(maxsize=32)
def _error_evaluation_for(path: str, mtime: float, quantity: str = "hourly_temp",
                          method: str = "idw") -> dict:
    """evaluate_error_models on the saved table, recomputed only when the file changes."""
    return evaluate_error_models(load_table(), method=method, quantity=quantity)


def expected_error_for(method: str, dist_km: float, elev_diff_m: float,
                       disagreement: float | None, quantity: str = "hourly_temp") -> dict | None:
    """Expected error at an orchard from whichever error model won on unseen stations.

    The models are scored separately for each method and quantity, so the disagreement signal
    is only used for a method if it actually predicted that method's error. Falls back to
    similar_stations if the winning model can't be used here (e.g. no disagreement value).
    """
    table = load_table()
    if table.empty:
        return None
    ev = _error_evaluation_for(str(config.VALIDATION_TABLE), config.VALIDATION_TABLE.stat().st_mtime,
                               quantity, method)
    if ev.get("status") == "no_validation_table":
        return None
    est = (predict_error(table, ev["best_model"], dist_km, elev_diff_m, disagreement, method, quantity=quantity)
           or predict_error(table, "similar_stations", dist_km, elev_diff_m, disagreement, method,
                            quantity=quantity))
    est["chosen_because"] = (f"lowest average miss on unseen stations "
                             f"({ev['scores'][ev['best_model']]['mean_miss']} {ev['unit']}, {ev['cases']} cases)")

    # Our tests only cover what they cover: flag orchards outside that range instead of
    # quietly extrapolating (e.g. a station 4 km away when every test case was 7+ km).
    outside = []
    lo, hi = table["nearest_dist_km"].min(), table["nearest_dist_km"].max()
    if not lo <= dist_km <= hi:
        outside.append(f"nearest station {dist_km:.1f} km away; tests covered {lo:.1f}-{hi:.1f} km")
    if est["model"].startswith("disagreement") and disagreement is not None:
        glo, ghi = table["idw_disagreement_C"].min(), table["idw_disagreement_C"].max()
        if not glo <= disagreement <= ghi:
            outside.append(f"disagreement {disagreement:.2f} °C; tests covered {glo:.2f}-{ghi:.2f} °C")
    est["within_tested_range"] = not outside
    if outside:
        est["warning"] = "Outside what we tested, so this error is an extrapolation: " + "; ".join(outside)
    return est


# ---------------------------------------------------------------- elevation rule, per metric
# Chill totals count hours inside temperature bands, so the method with the lowest hourly
# error is not always the one with the lowest chill error. And where the orchard sits well
# above or below the nearest station, a fixed lapse rate can go the wrong way (winter
# inversions) while Open-Meteo models the difference hour by hour. So for each metric we learn
# one simple rule: "below X m of elevation difference use method A, at or above X use B", and
# keep it only if it beats the best single method on stations it never saw.

ELEV_THRESHOLDS_M = (25, 50, 75, 100, 125, 150)
MIN_CASES_PER_SIDE = 3


def _abs_err(df: pd.DataFrame, quantity: str, method: str) -> pd.Series:
    return df[QUANTITIES[quantity][0].format(m=method)].abs()


def learn_elevation_rule(table: pd.DataFrame, quantity: str) -> dict | None:
    """Best threshold and the best method on each side of it, for one metric."""
    elev = table["nearest_elev_diff_m"].abs()
    best = None
    for th in ELEV_THRESHOLDS_M:
        below, above = table[elev < th], table[elev >= th]
        if len(below) < MIN_CASES_PER_SIDE or len(above) < MIN_CASES_PER_SIDE:
            continue
        m_below = min(METHODS, key=lambda m: _abs_err(below, quantity, m).mean())
        m_above = min(METHODS, key=lambda m: _abs_err(above, quantity, m).mean())
        total = (_abs_err(below, quantity, m_below).sum() + _abs_err(above, quantity, m_above).sum()) / len(table)
        if best is None or total < best["mean_error"]:
            best = {"threshold_m": th, "below": m_below, "above": m_above, "mean_error": total,
                    "cases_below": len(below), "cases_above": len(above)}
    return best


def evaluate_elevation_rule(table: pd.DataFrame, quantity: str) -> dict:
    """Does the elevation rule beat always using one method, on stations it never saw?

    For each station the rule (threshold and both methods) is learned from the OTHER stations,
    then applied here.
    """
    if table.empty or QUANTITIES[quantity][0].format(m="idw") not in table:
        return {"status": "no_validation_table"}
    errors, learned = [], []
    for sid, test in table.groupby("station_id"):
        rule = learn_elevation_rule(table[table["station_id"] != sid], quantity)
        if rule is None:
            continue
        learned.append((rule["threshold_m"], rule["below"], rule["above"]))
        for _, r in test.iterrows():
            m = rule["above"] if abs(r["nearest_elev_diff_m"]) >= rule["threshold_m"] else rule["below"]
            errors.append(abs(r[QUANTITIES[quantity][0].format(m=m)]))
    fixed = {m: round(float(_abs_err(table, quantity, m).mean()), 3) for m in METHODS}
    best_fixed = min(fixed, key=fixed.get)
    rule_err = round(float(np.mean(errors)), 3)
    counts = pd.Series([f"<{th} m: {a}, >={th} m: {b}" for th, a, b in learned]).value_counts()
    return {"quantity": quantity, "unit": QUANTITIES[quantity][1], "cases": len(errors),
            "elevation_rule_mean_error": rule_err, "always_method_mean_error": fixed,
            "best_fixed_method": best_fixed, "gain_vs_best_fixed": round(fixed[best_fixed] - rule_err, 3),
            "passed": rule_err < fixed[best_fixed],
            "rule_learned_most_often": counts.index[0], "rule_stability": f"{counts.iloc[0]} of {len(learned)}"}


@lru_cache(maxsize=16)
def _elevation_evaluation_for(path: str, mtime: float, quantity: str) -> dict:
    """evaluate_elevation_rule on the saved table, recomputed only when the file changes."""
    return evaluate_elevation_rule(load_table(), quantity)


def recommend_by_metric(dist_km: float, elev_diff_m: float, disagreement: float | None) -> dict:
    """Method to use for each metric at this orchard, plus its expected error.

    Uses the elevation rule where it beat the best single method on unseen stations;
    elsewhere uses that single method. Every choice says why.
    """
    table = load_table()
    if table.empty:
        return {}
    mtime = config.VALIDATION_TABLE.stat().st_mtime
    out = {}
    for q in QUANTITIES:
        ev = _elevation_evaluation_for(str(config.VALIDATION_TABLE), mtime, q)
        if ev.get("status") == "no_validation_table":
            continue
        if ev["passed"]:
            rule = learn_elevation_rule(table, q)
            side = "above" if abs(elev_diff_m) >= rule["threshold_m"] else "below"
            method = rule[side]
            reason = (f"Elevation difference {abs(elev_diff_m):.0f} m is "
                      f"{'at or above' if side == 'above' else 'below'} {rule['threshold_m']} m, "
                      f"where {method} had the lowest {q} error. This rule beat always using "
                      f"{ev['best_fixed_method']} on unseen stations ({ev['elevation_rule_mean_error']} vs "
                      f"{ev['always_method_mean_error'][ev['best_fixed_method']]} {ev['unit']}).")
            rule_text = f"<{rule['threshold_m']} m: {rule['below']}, >={rule['threshold_m']} m: {rule['above']}"
        else:
            method = ev["best_fixed_method"]
            reason = (f"{method} had the lowest {q} error on unseen stations; switching by elevation "
                      f"did not beat it ({ev['elevation_rule_mean_error']} vs "
                      f"{ev['always_method_mean_error'][method]} {ev['unit']}).")
            rule_text = None
        out[q] = {"method": method, "reason": reason, "elevation_rule": rule_text,
                  "expected_error": expected_error_for(method, dist_km, elev_diff_m, disagreement, q),
                  "check": {k: ev[k] for k in ("elevation_rule_mean_error", "always_method_mean_error",
                                               "passed", "rule_stability", "cases")}}
    return out


def evaluate_selector(table: pd.DataFrame, k: int | None = None,
                      min_margin_c: float | None = None) -> dict:
    """Does choosing per orchard beat always using one method? Tested on unseen stations.

    For each station: build the selector from every OTHER station's rows, let it choose for
    this station, and record the error the chosen method really had here. Compare with always
    using one method, and with the 'oracle' (the best method picked after seeing the answer).
    """
    if table.empty:
        return {"status": "no_validation_table"}
    picks = []
    for sid, rows in table.groupby("station_id"):
        others = table[table["station_id"] != sid]
        for _, row in rows.iterrows():
            choice = select_source(others, row["nearest_dist_km"], row["nearest_elev_diff_m"], k, min_margin_c)
            m = choice["selected_method"]
            mae = {x: row[f"mae_C_{x}"] for x in METHODS}
            cp = {x: abs(row[f"chill_portions_error_{x}"]) for x in METHODS}
            picks.append({"station_id": sid, "season_end": row["season_end"], "chosen": m,
                          "status": choice["status"], "mae_C": mae[m], "cp_abs_error": cp[m],
                          "oracle_mae_C": min(mae.values()),
                          **{f"mae_C_{x}": mae[x] for x in METHODS},
                          **{f"cp_abs_error_{x}": cp[x] for x in METHODS}})
    p = pd.DataFrame(picks)

    def summary(mae_col, cp_col):
        return {"mean_mae_C": round(float(p[mae_col].mean()), 3),
                "mean_chill_portions_abs_error": round(float(p[cp_col].mean()), 2) if cp_col else None}

    strategies = {"selector": summary("mae_C", "cp_abs_error"),
                  **{f"always_{x}": summary(f"mae_C_{x}", f"cp_abs_error_{x}") for x in METHODS},
                  "oracle (not achievable)": summary("oracle_mae_C", None)}
    best_fixed = min(METHODS, key=lambda x: strategies[f"always_{x}"]["mean_mae_C"])
    return {
        "cases": len(p),
        "stations": int(p["station_id"].nunique()),
        "strategies": strategies,
        "best_fixed_method": best_fixed,
        "selector_gain_vs_best_fixed_C": round(
            strategies[f"always_{best_fixed}"]["mean_mae_C"] - strategies["selector"]["mean_mae_C"], 3),
        "choices": p["chosen"].value_counts().to_dict(),
        "per_case": p[["station_id", "season_end", "chosen", "status", "mae_C", "oracle_mae_C"]]
                    .round(2).to_dict("records"),
        "note": "Each station is judged by a selector built without it (nested leave-one-station-out).",
    }
