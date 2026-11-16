"""The pre-trend check and the check one year earlier (docs/eval_plan.md, "Estimator").

`two_by_two` is the plan's 2x2 estimate with both bootstrap intervals. The check one
year earlier runs it on the placebo windows; the main estimate can reuse it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import numpy.typing as npt

from . import bootstrap as bs
from .calendar import day_range, month_of, parse_dates, week_start, window_days
from .outcomes import BY_NAME, OUTCOMES, Outcome, did, gap, group_value
from .panel import Array, Cube

Index = npt.NDArray[np.intp]
Interval = tuple[float, float]


def zone_period_sums(cube: Cube, periods: list[Index]) -> Array:
    """(zones, periods, measures) sums."""
    return np.stack([cube.values[idx].sum(axis=0) for idx in periods], axis=1)


def group_day_sums(cube: Cube) -> Array:
    """(days, 2, measures): treated total, then control total, for each day."""
    return np.stack([cube.treated.sum(axis=1), cube.control.sum(axis=1)], axis=1)


def _split(cube: Cube, zone_sums: Array) -> tuple[Array, Array]:
    n = cube.n_treated
    return zone_sums[:n].sum(axis=0), zone_sums[n:].sum(axis=0)


def threshold_rule(o: Outcome, p: dict[str, Any], check: str) -> tuple[float | None, str]:
    """The size line a check uses for this outcome, and the rule in words.

    Only trip counts have a size line in the plan. For the other outcomes the plan sets
    none, so the check fails on the interval alone (ADR 0008).
    """
    if o.kind != "ratio":
        return None, "fails if the interval leaves out zero (the plan sets no size line)"
    if check == "pretrend":
        line = float(p["pretrend"]["max_drift"])
        return line, f"fails if the slope interval leaves out zero and |drift| >= {line}"
    line = float(p["smallest_effect_of_interest"])
    return line, f"fails if the interval leaves out zero and |effect| >= {line}"


def check_result(leaves_out_zero: bool, size: float, line: float | None) -> str:
    big = True if line is None else abs(size) >= line
    return "fail" if leaves_out_zero and big else "pass"


def trend_projection(months: list[str]) -> Array:
    """Weights that turn a monthly series into its least-squares trend slope.

    The model is a month-of-year effect plus a straight-line trend (one step per month).
    """
    moy = [int(m[5:7]) for m in months]
    cols = sorted(set(moy))
    x = np.zeros((len(months), len(cols) + 1))
    for i, m in enumerate(moy):
        x[i, cols.index(m)] = 1.0
    x[:, -1] = np.arange(len(months), dtype=float)
    if np.linalg.matrix_rank(x) < x.shape[1]:
        raise ValueError("the pre-trend model needs at least two years of months")
    return np.asarray(np.linalg.pinv(x)[-1])


def month_days(month: str) -> list[date]:
    first = date(int(month[:4]), int(month[5:7]), 1)
    last = date(first.year + first.month // 12, first.month % 12 + 1, 1) - timedelta(days=1)
    return day_range(first, last)


def pretrend(
    cube: Cube, p: dict[str, Any], weights: Array, slope_draws: dict[str, Array] | None = None
) -> dict[str, Any]:
    """Fit the trend on the monthly treated-vs-control series and apply the plan's rule.

    If `slope_draws` is given, each outcome's zone-bootstrap slopes are stored in it, so
    the drift-adjusted estimate can use the same zone draws as the main estimate.
    """
    first, last = p["windows"]["pretrend_months"]
    months = sorted({month_of(d) for d in cube.days if first <= month_of(d) <= last})
    periods = [cube.day_index(month_days(m)) for m in months]
    c = trend_projection(months)
    level = float(p["bootstrap"]["interval_level"])
    rounds, seed = int(p["bootstrap"]["rounds"]), int(p["bootstrap"]["seed"])
    drift_months = float(p["pretrend"]["drift_months"])

    zsums = zone_period_sums(cube, periods)
    t_full, c_full = _split(cube, zsums)
    n_full = np.array([len(i) for i in periods], dtype=float)
    t_zone, c_zone = bs.zone_group_sums(weights, zsums, cube.n_treated)
    strata = [bs.make_stratum([cube.days[i] for i in idx], idx) for idx in periods]
    w_sums, w_days = bs.week_draws(group_day_sums(cube), strata, rounds, seed)

    out: dict[str, Any] = {"months": months, "outcomes": {}}
    for o in OUTCOMES:
        tv, cv = group_value(o, t_full, n_full), group_value(o, c_full, n_full)
        series = gap(o, tv, cv)
        slope = float(series @ c)
        zone_slopes = gap(o, group_value(o, t_zone, n_full), group_value(o, c_zone, n_full)) @ c
        w_t = group_value(o, w_sums[:, :, 0], w_days)
        w_c = group_value(o, w_sums[:, :, 1], w_days)
        week_slopes = gap(o, w_t, w_c) @ c
        if slope_draws is not None:
            slope_draws[o.name] = zone_slopes
        zone_iv = bs.interval(zone_slopes, level)
        week_iv = bs.interval(week_slopes, level)
        excl = bs.decide_excludes_zero(zone_iv, week_iv)
        line, rule = threshold_rule(o, p, "pretrend")
        drift = slope * drift_months
        out["outcomes"][o.name] = {
            "kind": o.kind,
            "unit": o.unit,
            "primary": o.primary,
            "treated_by_month": [float(v) for v in tv],
            "control_by_month": [float(v) for v in cv],
            "gap_by_month": [float(v) for v in series],
            "slope_per_month": slope,
            "slope_interval_zone": list(zone_iv),
            "slope_interval_week": list(week_iv),
            "drift": drift,
            "drift_interval_zone": [v * drift_months for v in zone_iv],
            "drift_interval_week": [v * drift_months for v in week_iv],
            "interval_leaves_out_zero": excl,
            "size_line": line,
            "rule": rule,
            "result": check_result(excl, drift, line),
        }
    return out


# One outcome's 2x2 estimate: the full-data value, the zone draws and the week draws.
Draws = tuple[float, Array, Array]


def did_draws(
    cube: Cube, p: dict[str, Any], weights: Array, pairs: list[tuple[list[date], list[date]]]
) -> list[dict[str, Draws]]:
    """The 2x2 estimate of every outcome for each (before days, after days) pair.

    All pairs share one zone draw (`weights`) and one week draw, so estimates built from
    two pairs, such as the triple difference, carry their shared noise.
    """
    windows = [days for pair in pairs for days in pair]
    periods = [cube.day_index(days) for days in windows]
    rounds, seed = int(p["bootstrap"]["rounds"]), int(p["bootstrap"]["seed"])
    zsums = zone_period_sums(cube, periods)
    t_full, c_full = _split(cube, zsums)
    n_full = np.array([len(days) for days in windows], dtype=float)
    t_zone, c_zone = bs.zone_group_sums(weights, zsums, cube.n_treated)
    strata = [bs.make_stratum(days, idx) for days, idx in zip(windows, periods, strict=True)]
    w_sums, w_days = bs.week_draws(group_day_sums(cube), strata, rounds, seed)

    def est(o: Outcome, t: Array, c: Array, n: Array) -> Array:
        tv, cv = group_value(o, t, n), group_value(o, c, n)
        return did(o, tv[..., 0], tv[..., 1], cv[..., 0], cv[..., 1])

    out = []
    for k in range(len(pairs)):
        sl = slice(2 * k, 2 * k + 2)
        out.append(
            {
                o.name: (
                    float(est(o, t_full[sl], c_full[sl], n_full[sl])),
                    est(o, t_zone[:, sl], c_zone[:, sl], n_full[sl]),
                    est(o, w_sums[:, sl, 0], w_sums[:, sl, 1], w_days[:, sl]),
                )
                for o in OUTCOMES
            }
        )
    return out


def two_by_two(
    cube: Cube,
    p: dict[str, Any],
    weights: Array,
    names: tuple[str, str],
    excluded: set[date],
) -> dict[str, Any]:
    """The 2x2 estimate for every outcome, with the zone and the week interval."""
    before = window_days(p, names[0], excluded)
    after = window_days(p, names[1], excluded)
    periods = [cube.day_index(before), cube.day_index(after)]
    level = float(p["bootstrap"]["interval_level"])
    zsums = zone_period_sums(cube, periods)
    t_full, c_full = _split(cube, zsums)
    n_full = np.array([len(before), len(after)], dtype=float)
    draws = did_draws(cube, p, weights, [(before, after)])[0]

    out: dict[str, Any] = {
        "before": [str(before[0]), str(before[-1])],
        "after": [str(after[0]), str(after[-1])],
        "days_kept": {"before": len(before), "after": len(after)},
        "week_blocks": {
            "before": len({week_start(d) for d in before}),
            "after": len({week_start(d) for d in after}),
        },
        "outcomes": {},
    }
    for o in OUTCOMES:
        tv, cv = group_value(o, t_full, n_full), group_value(o, c_full, n_full)
        effect, zone, week = draws[o.name]
        zone_iv = bs.interval(zone, level)
        week_iv = bs.interval(week, level)
        row: dict[str, Any] = {
            "kind": o.kind,
            "unit": o.unit,
            "primary": o.primary,
            "treated_before": float(tv[0]),
            "treated_after": float(tv[1]),
            "control_before": float(cv[0]),
            "control_after": float(cv[1]),
            "effect": effect,
            "interval_zone": list(zone_iv),
            "interval_week": list(week_iv),
            "interval_leaves_out_zero": bs.decide_excludes_zero(zone_iv, week_iv),
        }
        if o.kind == "ratio":
            row["effect_log"] = float(np.log1p(effect))
        out["outcomes"][o.name] = row
    return out


def placebo(cube: Cube, p: dict[str, Any], weights: Array) -> dict[str, Any]:
    """The check one year earlier, with the plan's holidays left out, then kept in."""
    names = ("placebo_before", "placebo_after")
    main = two_by_two(cube, p, weights, names, parse_dates(p["excluded_dates"]["placebo"]))
    for name, row in main["outcomes"].items():
        line, rule = threshold_rule(BY_NAME[name], p, "placebo")
        row["size_line"] = line
        row["rule"] = rule
        row["result"] = check_result(row["interval_leaves_out_zero"], row["effect"], line)
    kept = two_by_two(cube, p, weights, names, set())
    return {"main": main, "holidays_kept": kept}


def weekly_gap(
    cube: Cube,
    p: dict[str, Any],
    weights: Array,
    names: tuple[str, str],
    excluded: set[date],
) -> dict[str, Any]:
    """Week by week log(treated / control) minus its mean over the before weeks.

    Primary outcome only, on the days the estimate keeps. The band is the zone bootstrap.
    """
    before = window_days(p, names[0], excluded)
    after = window_days(p, names[1], excluded)
    in_before = set(before)
    weeks: dict[date, list[date]] = {}
    for d in before + after:
        weeks.setdefault(week_start(d), []).append(d)
    starts = sorted(weeks)
    periods = [cube.day_index(weeks[w]) for w in starts]
    is_before = np.array([weeks[w][0] in in_before for w in starts])
    zsums = zone_period_sums(cube, periods)
    t_full, c_full = _split(cube, zsums)
    t_zone, c_zone = bs.zone_group_sums(weights, zsums, cube.n_treated)

    def norm(t: Array, c: Array) -> Array:
        g = np.log(t[..., 0] / c[..., 0])  # measure 0 is trips, both platforms
        return np.asarray(g - g[..., is_before].mean(axis=-1, keepdims=True))

    full = norm(t_full, c_full)
    boot = norm(t_zone, c_zone)
    tail = (1.0 - float(p["bootstrap"]["interval_level"])) / 2.0
    lo, hi = np.quantile(boot, [tail, 1.0 - tail], axis=0)
    return {
        "week_start": [str(w) for w in starts],
        "in_before_window": [bool(v) for v in is_before],
        "days_kept": [len(weeks[w]) for w in starts],
        "gap": [float(v) for v in full],
        "band_low": [float(v) for v in lo],
        "band_high": [float(v) for v in hi],
    }
