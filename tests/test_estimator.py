"""The estimator on synthetic panels with a known effect (docs/eval_plan.md, "Estimator").

The fake panel uses the plan's real windows and holidays. Trips into the treated zones
fall by exactly 10% from the start of the after window, and the base fare rises by
exactly 2 dollars. Every other outcome is flat. The code must find those numbers.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import numpy as np
import pytest

from nyc_rideshare_congestion import params
from nyc_rideshare_congestion.analysis import bootstrap as bs
from nyc_rideshare_congestion.analysis import calendar, estimate, outcomes, prechecks
from nyc_rideshare_congestion.analysis.panel import MEASURES, Cube, PanelError

N_TREATED, N_CONTROL = 5, 8
EFFECT = -0.10
FARE_JUMP = 2.0


@pytest.fixture(scope="module")
def p() -> dict[str, Any]:
    plan = copy.deepcopy(params.load_params())
    plan["bootstrap"]["rounds"] = 200  # the plan's 2000 is for the real run; tests stay fast
    return plan


def make_cube(
    p: dict[str, Any], effect: float = EFFECT, drift_per_month: float = 0.0, seed: int = 0
) -> Cube:
    """Fake panel sums with a known effect, a yearly cycle and small zone-day noise."""
    rng = np.random.default_rng(seed)
    days = calendar.day_range(date(2023, 1, 1), date(2025, 3, 31))
    start = calendar.window(p, "after")[0]
    n_zones = N_TREATED + N_CONTROL
    base = rng.uniform(200, 2000, size=n_zones)
    values = np.zeros((len(days), n_zones, len(MEASURES)))
    for i, d in enumerate(days):
        season = 1.0 + 0.2 * np.sin(2 * np.pi * d.timetuple().tm_yday / 365)
        weekly = 1.1 if d.weekday() < 5 else 0.8
        months = (d.year - 2023) * 12 + d.month - 1 + (d.day - 1) / 30
        after = d >= start
        for z in range(n_zones):
            treated = z < N_TREATED
            trips = base[z] * season * weekly * (1 + rng.normal(0, 0.02))
            if treated:
                trips *= np.exp(drift_per_month * months)
                if after:
                    trips *= 1 + effect
            fare = 20.0 + (FARE_JUMP if treated and after else 0.0)
            values[i, z] = [
                trips,
                0.75 * trips,
                0.25 * trips,
                fare * trips,
                (fare + 3.0) * trips,
                900.0 * trips,
                3.0 * trips,
            ]
    zones = tuple(range(1, n_zones + 1))
    return Cube(days=days, zones=zones, n_treated=N_TREATED, values=values)


@pytest.fixture(scope="module")
def cube(p: dict[str, Any]) -> Cube:
    return make_cube(p)


@pytest.fixture(scope="module")
def weights(p: dict[str, Any]) -> Any:
    b = p["bootstrap"]
    return bs.zone_weights(N_TREATED, N_CONTROL, int(b["rounds"]), int(b["seed"]))


def test_calendar_expands_ranges_and_weeks() -> None:
    days = calendar.parse_dates(["2024-12-30/2025-01-02", "2025-01-20"])
    assert len(days) == 5
    assert calendar.week_start(date(2025, 1, 5)) == date(2024, 12, 30)
    assert calendar.month_of(date(2025, 3, 9)) == "2025-03"


def test_main_estimate_recovers_the_known_effect(
    p: dict[str, Any], cube: Cube, weights: Any
) -> None:
    excluded = calendar.parse_dates(p["excluded_dates"]["study"])
    out = prechecks.two_by_two(cube, p, weights, ("before", "after"), excluded)
    trips = out["outcomes"]["trips_per_day_both_platforms"]
    assert trips["effect"] == pytest.approx(EFFECT, abs=0.005)
    assert trips["interval_leaves_out_zero"]
    assert trips["interval_zone"][0] < trips["effect"] < trips["interval_zone"][1]
    fare = out["outcomes"]["base_fare_per_trip"]
    assert fare["effect"] == pytest.approx(FARE_JUMP, abs=1e-9)
    assert out["outcomes"]["lyft_share"]["effect"] == pytest.approx(0.0, abs=1e-9)
    assert out["outcomes"]["minutes_per_mile"]["effect"] == pytest.approx(0.0, abs=1e-9)
    assert out["days_kept"]["after"] == 84  # 86 days minus two holidays


def test_both_checks_pass_when_there_is_no_drift(
    p: dict[str, Any], cube: Cube, weights: Any
) -> None:
    pre = prechecks.pretrend(cube, p, weights)
    row = pre["outcomes"]["trips_per_day_both_platforms"]
    assert len(pre["months"]) == 24
    assert abs(row["drift"]) < 0.005
    assert row["result"] == "pass"
    plc = prechecks.placebo(cube, p, weights)
    assert plc["main"]["outcomes"]["trips_per_day_both_platforms"]["result"] == "pass"
    assert abs(plc["main"]["outcomes"]["trips_per_day_both_platforms"]["effect"]) < 0.005


def test_pretrend_check_fails_on_a_known_drift(p: dict[str, Any], weights: Any) -> None:
    drifting = make_cube(p, drift_per_month=0.01, seed=1)
    row = prechecks.pretrend(drifting, p, weights)["outcomes"]["trips_per_day_both_platforms"]
    assert row["slope_per_month"] == pytest.approx(0.01, abs=0.001)
    assert row["drift"] >= p["pretrend"]["max_drift"]
    assert row["result"] == "fail"


def test_trend_projection_finds_the_slope_and_needs_two_years() -> None:
    months = [f"{y}-{m:02d}" for y in (2023, 2024) for m in range(1, 13)]
    series = np.array([0.5 * (i % 12 == 6) + 0.02 * i for i in range(24)])
    assert float(series @ prechecks.trend_projection(months)) == pytest.approx(0.02)
    with pytest.raises(ValueError, match="two years"):
        prechecks.trend_projection(months[:12])


def test_full_estimate_reports_every_part(p: dict[str, Any], cube: Cube, weights: Any) -> None:
    slopes: dict[str, Any] = {}
    pre = prechecks.pretrend(cube, p, weights, slopes)
    plc = prechecks.placebo(cube, p, weights)
    fees: dict[str, Any] = {"flags": {}}
    out = estimate.run_estimate(cube, p, weights, pre, slopes, plc, fees)
    assert out["verdict"]["table_rows"] == ["leaves_out_zero_checks_pass"]
    triple = out["triple_difference"]["trips_per_day_both_platforms"]
    assert triple["effect"] == pytest.approx(np.log1p(EFFECT), abs=0.01)
    assert triple["interval_week"] is not None
    adj = out["drift_adjusted"]["trips_per_day_both_platforms"]
    assert adj["effect"] == pytest.approx(EFFECT, abs=0.01)
    assert adj["interval_week"] is None
    weeks = out["event_study"]["study"]
    after = [g for g, b in zip(weeks["gap"], weeks["in_before_window"], strict=True) if not b]
    assert np.mean(after) == pytest.approx(np.log1p(EFFECT), abs=0.01)
    assert out["holidays_kept"]["days_kept"]["after"] > out["main"]["days_kept"]["after"]


def _row(lo: float, hi: float, excl: bool) -> dict[str, Any]:
    return {"interval_zone": [lo, hi], "interval_week": [lo, hi], "interval_leaves_out_zero": excl}


@pytest.mark.parametrize(
    ("row", "pre", "plc", "expected"),
    [
        (_row(-0.2, -0.1, True), "pass", "pass", ["leaves_out_zero_checks_pass"]),
        (_row(-0.2, -0.1, True), "fail", "pass", ["leaves_out_zero_pretrend_fails"]),
        (_row(-0.2, -0.1, True), "pass", "fail", ["leaves_out_zero_placebo_fails"]),
        (
            _row(-0.2, -0.1, True),
            "fail",
            "fail",
            ["leaves_out_zero_pretrend_fails", "leaves_out_zero_placebo_fails"],
        ),
        (_row(-0.02, 0.02, False), "pass", "pass", ["holds_zero_inside_smallest_effect"]),
        (_row(-0.05, 0.02, False), "pass", "pass", ["holds_zero_wider_than_smallest_effect"]),
    ],
)
def test_verdict_picks_the_plan_table_row(
    row: dict[str, Any], pre: str, plc: str, expected: list[str]
) -> None:
    got = estimate.verdict(row, {"result": pre}, {"result": plc}, 0.03)
    assert got["table_rows"] == expected


def test_verdict_uses_the_wider_interval_for_the_size_test() -> None:
    row = {
        "interval_zone": [-0.01, 0.01],
        "interval_week": [-0.04, 0.02],
        "interval_leaves_out_zero": False,
    }
    got = estimate.verdict(row, {"result": "pass"}, {"result": "pass"}, 0.03)
    assert got["interval_used"] == [-0.04, 0.02]
    assert got["table_rows"] == ["holds_zero_wider_than_smallest_effect"]


def test_bootstrap_helpers() -> None:
    w = bs.zone_weights(3, 4, rounds=50, seed=1)
    assert w.shape == (50, 7)
    assert np.all(w[:, :3].sum(axis=1) == 3) and np.all(w[:, 3:].sum(axis=1) == 4)
    assert np.array_equal(w, bs.zone_weights(3, 4, rounds=50, seed=1))
    assert bs.excludes_zero((0.1, 0.2)) and not bs.excludes_zero((-0.1, 0.2))
    # The two intervals disagree on zero: the wider one decides.
    assert not bs.decide_excludes_zero((0.01, 0.02), (-0.05, 0.05))
    assert bs.decide_excludes_zero((0.01, 0.2), (-0.01, 0.05))
    with pytest.raises(ValueError, match="NaN"):
        bs.interval(np.array([1.0, np.nan]), 0.95)


def test_outcome_math_by_hand() -> None:
    o = outcomes.BY_NAME["trips_per_day_both_platforms"]
    assert float(
        outcomes.did(o, np.array(100.0), np.array(90.0), np.array(50.0), np.array(50.0))
    ) == pytest.approx(-0.1)
    fare = outcomes.BY_NAME["base_fare_per_trip"]
    assert float(
        outcomes.did(fare, np.array(20.0), np.array(23.0), np.array(20.0), np.array(21.0))
    ) == pytest.approx(2.0)


def test_cube_refuses_missing_days(cube: Cube) -> None:
    with pytest.raises(PanelError, match="no cells"):
        cube.day_index([date(2022, 1, 1)])
