"""The longer window, compared year over year (ADR 0009).

Every rule here comes from docs/decisions/0009-deviation-longer-window.md. The plan's
own study (estimate.py) is not touched. This module reuses its estimator, its outcomes
and its two bootstraps, with windows that sit in the same season on both sides.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import numpy as np
import numpy.typing as npt

from . import bootstrap as bs
from .calendar import day_range, month_of
from .outcomes import BY_NAME, OUTCOMES, Outcome, did, group_value
from .panel import Array, Cube
from .prechecks import (
    Draws,
    _split,
    check_result,
    group_day_sums,
    month_days,
    threshold_rule,
    zone_period_sums,
)

PRIMARY = "trips_per_day_both_platforms"
FEE_START = date(2025, 1, 5)
SERIES_FIRST_MONTH = "2023-01"
LAG_MONTHS = 12

# ADR 0009, "Year-over-year estimate". Dates are inclusive.
WINDOWS: dict[str, tuple[str, str]] = {
    "before": ("2024-01-05", "2024-12-31"),
    "after": ("2025-01-05", "2025-12-31"),
    "placebo_before": ("2023-01-05", "2023-12-31"),
    "placebo_after": ("2024-01-05", "2024-12-31"),
}
# ADR 0009, "Second fee year": the first day of each side.
SECOND_YEAR_FIRST = ("2025-01-05", "2026-01-05")


def days_of(name: str) -> list[date]:
    first, last = WINDOWS[name]
    return day_range(date.fromisoformat(first), date.fromisoformat(last))


def stop_rule_params(p: dict[str, Any]) -> dict[str, Any]:
    """The plan's parameters with the year-over-year windows and no holiday list."""
    out = copy.deepcopy(p)
    out["windows"] = {**p["windows"], **{k: list(v) for k, v in WINDOWS.items()}}
    out["excluded_dates"] = {"study": [], "placebo": []}
    return out


def second_year_days(last_day: date) -> tuple[list[date], list[date]]:
    """The second fee year up to the last loaded day, and the same days of 2025."""
    first_2025, first_2026 = (date.fromisoformat(d) for d in SECOND_YEAR_FIRST)
    after = day_range(first_2026, last_day)
    before = day_range(first_2025, date(last_day.year - 1, last_day.month, last_day.day))
    return before, after


BLOCK_DAYS = 7

Sums = tuple[Array, Array, Array, Array, Array, Array, Array]


def matched_week_draws(
    day_sums: Array, periods: list[npt.NDArray[np.intp]], rounds: int, seed: int
) -> tuple[Array, Array]:
    """Resample seven-day blocks, with the same block numbers in every window.

    ADR 0009, addendum: each window is cut into blocks of seven days from its first
    day. One draw of block numbers is used for all windows, so a drawn week of one year
    is always set against the same week of the other years. Returns sums of shape
    (rounds, windows, ...) and day counts of shape (rounds, windows).
    """
    n_blocks = {-(-len(idx) // BLOCK_DAYS) for idx in periods}
    if len(n_blocks) != 1:
        raise ValueError("matched week draws need windows with the same number of blocks")
    blocks = n_blocks.pop()
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, blocks, size=(rounds, blocks))
    counts = np.zeros((rounds, blocks))
    np.add.at(counts, (np.repeat(np.arange(rounds), blocks), picks.ravel()), 1.0)
    sums, n_days = [], []
    for idx in periods:
        cuts = [idx[k * BLOCK_DAYS : (k + 1) * BLOCK_DAYS] for k in range(blocks)]
        block_sums = np.stack([day_sums[c].sum(axis=0) for c in cuts])
        block_days = np.array([len(c) for c in cuts], dtype=float)
        flat = block_sums.reshape(blocks, -1)
        sums.append((counts @ flat).reshape(rounds, *day_sums.shape[1:]))
        n_days.append(counts @ block_days)
    return np.stack(sums, axis=1), np.stack(n_days, axis=1)


def window_draws(cube: Cube, p: dict[str, Any], weights: Array, windows: list[list[date]]) -> Sums:
    """Group sums for each window: full data, zone draws and matched week draws.

    Each window is drawn once. The year 2024 is the before side of the main estimate
    and the after side of the check one year earlier, and both use the same draw.
    """
    periods = [cube.day_index(days) for days in windows]
    rounds, seed = int(p["bootstrap"]["rounds"]), int(p["bootstrap"]["seed"])
    zsums = zone_period_sums(cube, periods)
    t_full, c_full = _split(cube, zsums)
    n_full = np.array([len(days) for days in windows], dtype=float)
    t_zone, c_zone = bs.zone_group_sums(weights, zsums, cube.n_treated)
    w_sums, w_days = matched_week_draws(group_day_sums(cube), periods, rounds, seed)
    return t_full, c_full, n_full, t_zone, c_zone, w_sums, w_days


def pair_draws(sums: Sums, i: int, j: int) -> dict[str, Draws]:
    """The 2x2 estimate of every outcome, window i (before) against window j (after)."""
    t_full, c_full, n_full, t_zone, c_zone, w_sums, w_days = sums
    ix = [i, j]

    def est(o: Outcome, t: Array, c: Array, n: Array) -> Array:
        tv, cv = group_value(o, t, n), group_value(o, c, n)
        return did(o, tv[..., 0], tv[..., 1], cv[..., 0], cv[..., 1])

    return {
        o.name: (
            float(est(o, t_full[ix], c_full[ix], n_full[ix])),
            est(o, t_zone[:, ix], c_zone[:, ix], n_full[ix]),
            est(o, w_sums[:, ix, 0], w_sums[:, ix, 1], w_days[:, ix]),
        )
        for o in OUTCOMES
    }


def _row(draws: Draws, level: float) -> dict[str, Any]:
    """One estimate with both intervals and the standard error from the zone draws."""
    full, zone, week = draws
    zone_iv, week_iv = bs.interval(zone, level), bs.interval(week, level)
    return {
        "effect": full,
        "se_zone": float(np.std(zone, ddof=1)),
        "interval_zone": list(zone_iv),
        "interval_week": list(week_iv),
        "interval_leaves_out_zero": bs.decide_excludes_zero(zone_iv, week_iv),
    }


def _estimate_block(
    cube: Cube, before: list[date], after: list[date], draws: dict[str, Draws], level: float
) -> dict[str, Any]:
    periods = [cube.day_index(before), cube.day_index(after)]
    t_full, c_full = _split(cube, zone_period_sums(cube, periods))
    n_full = np.array([len(before), len(after)], dtype=float)
    out: dict[str, Any] = {
        "before": [str(before[0]), str(before[-1])],
        "after": [str(after[0]), str(after[-1])],
        "days_kept": {"before": len(before), "after": len(after)},
        "outcomes": {},
    }
    for o in OUTCOMES:
        tv, cv = group_value(o, t_full, n_full), group_value(o, c_full, n_full)
        row = {
            "kind": o.kind,
            "unit": o.unit,
            "primary": o.primary,
            "treated_before": float(tv[0]),
            "treated_after": float(tv[1]),
            "control_before": float(cv[0]),
            "control_after": float(cv[1]),
            **_row(draws[o.name], level),
        }
        if o.kind == "ratio":
            row["effect_log"] = float(np.log1p(row["effect"]))
        out["outcomes"][o.name] = row
    return out


def triple_difference(
    main: dict[str, Draws], placebo: dict[str, Draws], level: float
) -> dict[str, Any]:
    """Main minus placebo: log scale for counts, in units otherwise (as in the plan)."""
    out: dict[str, Any] = {}
    for o in OUTCOMES:
        (mf, mz, mw), (pf, pz, pw) = main[o.name], placebo[o.name]
        if o.kind == "ratio":
            draws: Draws = (
                float(np.log1p(mf) - np.log1p(pf)),
                np.log1p(mz) - np.log1p(pz),
                np.log1p(mw) - np.log1p(pw),
            )
            row = _row(draws, level)
            row["scale"] = "log points"
            row["as_percent"] = float(np.expm1(row["effect"]))
            for key in ("interval_zone", "interval_week"):
                row[f"{key}_as_percent"] = [float(np.expm1(v)) for v in row[key]]
        else:
            row = _row((mf - pf, mz - pz, mw - pw), level)
            row["scale"] = o.unit
        out[o.name] = row
    return out


def verdict(triple_row: dict[str, Any], placebo_row: dict[str, Any], sei: float) -> dict[str, Any]:
    """The plan's "What the memo may say" table, applied to the triple difference.

    The wider of the two intervals is tested against the smallest effect of interest,
    on the percent scale (ADR 0008 does the same for the plan's own estimate).
    """
    zone_iv = triple_row["interval_zone_as_percent"]
    week_iv = triple_row["interval_week_as_percent"]
    wider = zone_iv if zone_iv[1] - zone_iv[0] >= week_iv[1] - week_iv[0] else week_iv
    excl = bool(triple_row["interval_leaves_out_zero"])
    if excl:
        row = "leaves_out_zero"
    elif -sei < wider[0] and wider[1] < sei:
        row = "holds_zero_inside_smallest_effect"
    else:
        row = "holds_zero_wider_than_smallest_effect"
    return {
        "table_row": row,
        "leads_with": "triple_difference",
        "interval_used_as_percent": list(wider),
        "interval_leaves_out_zero": excl,
        "check_one_year_earlier": placebo_row["result"],
        "smallest_effect_of_interest": sei,
    }


def _series_days(month: str) -> list[date]:
    """Days of one month. January 2025 starts on the first day of the fee (ADR 0009)."""
    days = month_days(month)
    if month == month_of(FEE_START):
        days = [d for d in days if d >= FEE_START]
    return days


def monthly_event_study(cube: Cube, weights: Array, level: float) -> dict[str, Any]:
    """log(treated / control) by month, minus the value twelve months earlier.

    Primary outcome only. The band is the zone bootstrap with the shared draws.
    """
    months = sorted({month_of(d) for d in cube.days if month_of(d) >= SERIES_FIRST_MONTH})
    periods = [cube.day_index(_series_days(m)) for m in months]
    zsums = zone_period_sums(cube, periods)
    t_full, c_full = _split(cube, zsums)
    t_zone, c_zone = bs.zone_group_sums(weights, zsums, cube.n_treated)
    n_days = np.array([len(i) for i in periods], dtype=float)

    def gap(t: Array, c: Array) -> Array:
        return np.asarray(np.log(t[..., 0] / c[..., 0]))  # measure 0 is trips

    def lagged(g: Array) -> Array:
        return np.asarray(g[..., LAG_MONTHS:] - g[..., :-LAG_MONTHS])

    full_gap = gap(t_full, c_full)
    full, boot = lagged(full_gap), lagged(gap(t_zone, c_zone))
    tail = (1.0 - level) / 2.0
    lo, hi = np.quantile(boot, [tail, 1.0 - tail], axis=0)
    shown = months[LAG_MONTHS:]
    fee_month = month_of(FEE_START)
    return {
        "months_all": months,
        "log_ratio_all": [float(v) for v in full_gap],
        "treated_trips_per_day": [float(v) for v in t_full[:, 0] / n_days],
        "control_trips_per_day": [float(v) for v in c_full[:, 0] / n_days],
        "months": shown,
        "fee_in_month": [m >= fee_month for m in shown],
        "fee_in_month_a_year_earlier": [months[i] >= fee_month for i in range(len(shown))],
        "change_from_a_year_earlier": [float(v) for v in full],
        "band_low": [float(v) for v in lo],
        "band_high": [float(v) for v in hi],
    }


def zone_changes(cube: Cube, before: list[date], after: list[date]) -> list[dict[str, Any]]:
    """Each zone's change in log trips per day, minus the change for all control zones.

    A description of where the change sits. No zone gets an interval or a test.
    """
    periods = [cube.day_index(before), cube.day_index(after)]
    trips = zone_period_sums(cube, periods)[:, :, 0]  # (zones, 2)
    per_day = trips / np.array([len(before), len(after)], dtype=float)
    control = per_day[cube.n_treated :].sum(axis=0)
    control_change = float(np.log(control[1] / control[0]))
    out: list[dict[str, Any]] = []
    for i, zone in enumerate(cube.zones):
        b, a = float(per_day[i, 0]), float(per_day[i, 1])
        ok = b > 0 and a > 0
        out.append(
            {
                "zone": int(zone),
                "group": "treated" if i < cube.n_treated else "control",
                "trips_per_day_before": b,
                "trips_per_day_after": a,
                "change_vs_control_log": float(np.log(a / b)) - control_change if ok else None,
            }
        )
    return out


def run_extended(cube: Cube, p: dict[str, Any], weights: Array) -> dict[str, Any]:
    """The year-over-year estimate and everything ADR 0009 says to report with it."""
    level = float(p["bootstrap"]["interval_level"])
    sei = float(p["smallest_effect_of_interest"])
    before, after = days_of("before"), days_of("after")
    plc_before, plc_after = days_of("placebo_before"), days_of("placebo_after")
    if plc_after != before:
        raise ValueError("ADR 0009: the check one year earlier must end on the before window")
    second_before, second_after = second_year_days(cube.days[-1])
    sums = window_draws(cube, p, weights, [plc_before, before, after])
    main_d, plc_d = pair_draws(sums, 1, 2), pair_draws(sums, 0, 1)
    second_d = pair_draws(window_draws(cube, p, weights, [second_before, second_after]), 0, 1)

    main = _estimate_block(cube, before, after, main_d, level)
    plc = _estimate_block(cube, plc_before, plc_after, plc_d, level)
    for name, row in plc["outcomes"].items():
        line, rule = threshold_rule(BY_NAME[name], p, "placebo")
        row["size_line"] = line
        row["rule"] = rule
        row["result"] = check_result(row["interval_leaves_out_zero"], row["effect"], line)
    second = _estimate_block(cube, second_before, second_after, second_d, level)
    second["label"] = "exploratory: both windows have the fee"
    triple = triple_difference(main_d, plc_d, level)
    return {
        "adr": "docs/decisions/0009-deviation-longer-window.md",
        "label": "amendment",
        "primary_outcome": PRIMARY,
        "last_day_loaded": str(cube.days[-1]),
        "verdict": verdict(triple[PRIMARY], plc["outcomes"][PRIMARY], sei),
        "year_over_year": main,
        "check_one_year_earlier": plc,
        "triple_difference": triple,
        "second_fee_year": second,
        "event_study_monthly": monthly_event_study(cube, weights, level),
        "zone_changes": zone_changes(cube, before, after),
    }
