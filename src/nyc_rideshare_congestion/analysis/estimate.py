"""The main estimate, its secondary versions, the flags and the verdict.

Every rule comes from docs/eval_plan.md ("Estimator", "Inference", "Flags" and "What
the memo may say"). The checks before the estimate are in prechecks.py. This module
only runs when the stop rules have passed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
import numpy as np

from . import bootstrap as bs
from .calendar import parse_dates, week_start, window, window_days
from .outcomes import BY_NAME, OUTCOMES
from .panel import Array, Cube
from .prechecks import Draws, did_draws, two_by_two, weekly_gap

PRIMARY = "trips_per_day_both_platforms"
Interval = tuple[float, float]


def fee_shares(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> dict[str, Any]:
    """Shares of kept treated and control trips with a fee above zero, after the start.

    Uses every day of the after window. The mean fee on treated trips is a fact the plan
    asks for, not an estimate.
    """
    first, last = window(p, "after")
    rows = con.execute(
        """
        SELECT zone_group, sum(trips), sum(cbd_fee_trips), sum(cbd_fee_sum)
        FROM panel_zone_day
        WHERE full_date BETWEEN ? AND ?
        GROUP BY 1
        """,
        [first, last],
    ).fetchall()
    got = {str(g): (float(n or 0), float(f or 0), float(s or 0)) for g, n, f, s in rows}
    flags = p["flags"]
    out: dict[str, Any] = {"window": [str(first), str(last)]}
    for group in ("treated", "control"):
        trips, fee_trips, fee_sum = got.get(group, (0.0, 0.0, 0.0))
        out[group] = {
            "trips": int(trips),
            "trips_with_fee": int(fee_trips),
            "fee_share": fee_trips / trips if trips else float("nan"),
            "mean_fee_dollars": fee_sum / trips if trips else float("nan"),
        }
    t_share, c_share = out["treated"]["fee_share"], out["control"]["fee_share"]
    out["flags"] = {
        "treated_fee_share_low": {
            "line": float(flags["min_treated_fee_share"]),
            "value": t_share,
            "raised": bool(t_share < float(flags["min_treated_fee_share"])),
        },
        "control_fee_share_high": {
            "line": float(flags["max_control_fee_share"]),
            "value": c_share,
            "raised": bool(c_share > float(flags["max_control_fee_share"])),
        },
    }
    return out


def summarize(full: float, zone: Array, week: Array | None, level: float) -> dict[str, Any]:
    """A value with its zone interval and, if given, its week interval."""
    zone_iv = bs.interval(zone, level)
    row: dict[str, Any] = {"effect": full, "interval_zone": list(zone_iv)}
    if week is None:
        row["interval_week"] = None
        row["interval_leaves_out_zero"] = bs.excludes_zero(zone_iv)
        return row
    week_iv = bs.interval(week, level)
    row["interval_week"] = list(week_iv)
    row["interval_leaves_out_zero"] = bs.decide_excludes_zero(zone_iv, week_iv)
    return row


def triple_difference(main: dict[str, Draws], placebo: dict[str, Draws], level: float) -> Any:
    """The main estimate minus the placebo estimate: log scale for counts, else in units."""
    out: dict[str, Any] = {}
    for o in OUTCOMES:
        (mf, mz, mw), (pf, pz, pw) = main[o.name], placebo[o.name]
        if o.kind == "ratio":
            row = summarize(
                float(np.log1p(mf) - np.log1p(pf)),
                np.log1p(mz) - np.log1p(pz),
                np.log1p(mw) - np.log1p(pw),
                level,
            )
            row["scale"] = "log points"
            row["as_percent"] = float(np.expm1(row["effect"]))
        else:
            row = summarize(mf - pf, mz - pz, mw - pw, level)
            row["scale"] = o.unit
        out[o.name] = row
    return out


def drift_adjusted(
    main: dict[str, Draws], pre: dict[str, Any], slopes: dict[str, Array], level: float
) -> Any:
    """The main estimate with the pre-trend drift taken out, using the same zone draws.

    Only the zone interval is given: the pre-trend months and the before window share
    days, so separate week draws for the two would not line up (ADR 0008).
    """
    months = float(pre["drift_months"])
    out: dict[str, Any] = {}
    for o in OUTCOMES:
        full, zone, _ = main[o.name]
        drift = float(pre["outcomes"][o.name]["drift"])
        zone_drift = slopes[o.name] * months
        if o.kind == "ratio":
            row = summarize(
                float(np.expm1(np.log1p(full) - drift)),
                np.expm1(np.log1p(zone) - zone_drift),
                None,
                level,
            )
        else:
            row = summarize(full - drift, zone - zone_drift, None, level)
        row["drift_removed"] = drift
        out[o.name] = row
    return out


def _wider(a: Interval, b: Interval) -> Interval:
    return a if a[1] - a[0] >= b[1] - b[0] else b


def verdict(
    main_row: dict[str, Any], pre_row: dict[str, Any], plc_row: dict[str, Any], sei: float
) -> dict[str, Any]:
    """Pick the row of the plan's "What the memo may say" table for the primary outcome.

    When the two intervals disagree on zero, the wider one decides (plan, "Inference").
    The test against the smallest effect of interest uses the wider interval as well.
    """
    zone_iv = (float(main_row["interval_zone"][0]), float(main_row["interval_zone"][1]))
    week_iv = (float(main_row["interval_week"][0]), float(main_row["interval_week"][1]))
    wider = _wider(zone_iv, week_iv)
    excl = bool(main_row["interval_leaves_out_zero"])
    pre_fail = pre_row["result"] == "fail"
    plc_fail = plc_row["result"] == "fail"
    if excl:
        rows = []
        if pre_fail:
            rows.append("leaves_out_zero_pretrend_fails")
        if plc_fail:
            rows.append("leaves_out_zero_placebo_fails")
        if not rows:
            rows.append("leaves_out_zero_checks_pass")
    elif -sei < wider[0] and wider[1] < sei:
        rows = ["holds_zero_inside_smallest_effect"]
    else:
        rows = ["holds_zero_wider_than_smallest_effect"]
    return {
        "table_rows": rows,
        "interval_used": list(wider),
        "interval_leaves_out_zero": excl,
        "pretrend_check": pre_row["result"],
        "placebo_check": plc_row["result"],
        "smallest_effect_of_interest": sei,
    }


def run_estimate(
    cube: Cube,
    p: dict[str, Any],
    weights: Array,
    pre: dict[str, Any],
    slopes: dict[str, Array],
    plc: dict[str, Any],
    fees: dict[str, Any],
) -> dict[str, Any]:
    """The main estimate and everything the plan says to report next to it."""
    level = float(p["bootstrap"]["interval_level"])
    sei = float(p["smallest_effect_of_interest"])
    excl_study = parse_dates(p["excluded_dates"]["study"])
    excl_placebo = parse_dates(p["excluded_dates"]["placebo"])

    main = two_by_two(cube, p, weights, ("before", "after"), excl_study)
    kept = two_by_two(cube, p, weights, ("before", "after"), set())
    pairs = [
        (window_days(p, "before", excl_study), window_days(p, "after", excl_study)),
        (
            window_days(p, "placebo_before", excl_placebo),
            window_days(p, "placebo_after", excl_placebo),
        ),
    ]
    main_draws, placebo_draws = did_draws(cube, p, weights, pairs)
    pre_ctx = {**pre, "drift_months": p["pretrend"]["drift_months"]}

    study_weeks = weekly_gap(cube, p, weights, ("before", "after"), excl_study)
    placebo_weeks = weekly_gap(cube, p, weights, ("placebo_before", "placebo_after"), excl_placebo)
    after_start = window(p, "after")[0]
    placebo_start = window(p, "placebo_after")[0]

    return {
        "primary_outcome": PRIMARY,
        "verdict": verdict(
            main["outcomes"][PRIMARY],
            pre["outcomes"][PRIMARY],
            plc["main"]["outcomes"][PRIMARY],
            sei,
        ),
        "main": main,
        "holidays_kept": kept,
        "triple_difference": triple_difference(main_draws, placebo_draws, level),
        "drift_adjusted": drift_adjusted(main_draws, pre_ctx, slopes, level),
        "fees_after_start": fees,
        "event_study": {
            "study": {**study_weeks, "after_starts": str(after_start)},
            "one_year_earlier": {**placebo_weeks, "after_starts": str(placebo_start)},
        },
        "secondary_outcomes": [o.name for o in OUTCOMES if not BY_NAME[o.name].primary],
    }


def stopped(stops: dict[str, Any]) -> dict[str, Any]:
    """What estimate.json holds when a stop rule failed: the cause and no outcome."""
    return {
        "primary_outcome": PRIMARY,
        "verdict": {"table_rows": ["stop_rule_fails"]},
        "failed_rules": stops.get("failed_rules", []),
        "pipeline_check_failures": stops.get("pipeline_check_failures", []),
        "note": "A stop rule failed, so no outcome is reported (docs/eval_plan.md).",
    }


def relative_weeks(week_starts: list[str], start: date) -> list[float]:
    """Weeks from the week that holds `start`, for lining up the two event studies."""
    origin = week_start(start)
    return [(date.fromisoformat(w) - origin).days / 7 for w in week_starts]
