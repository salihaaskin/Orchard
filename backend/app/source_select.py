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
