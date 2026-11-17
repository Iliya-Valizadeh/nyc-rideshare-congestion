"""The year-over-year estimate on a synthetic panel with a known effect (ADR 0009).

The fake panel has what broke the plan's first design: the treated zones have a
stronger yearly cycle than the control zones, and they drift down a little every
month. Trips into the treated zones then fall by exactly 10% from January 5, 2025.
The triple difference must find the 10% and nothing else.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import numpy as np
import pytest

from nyc_rideshare_congestion import params
from nyc_rideshare_congestion.analysis import bootstrap as bs
from nyc_rideshare_congestion.analysis import calendar, extended
from nyc_rideshare_congestion.analysis.panel import MEASURES, Cube

N_TREATED, N_CONTROL = 5, 8
EFFECT = -0.10
DRIFT = -0.004  # log points per month, treated zones only
LAST_DAY = date(2026, 9, 30)


@pytest.fixture(scope="module")
def p() -> dict[str, Any]:
    plan = copy.deepcopy(params.load_params())
    plan["bootstrap"]["rounds"] = 200
    return plan


def make_cube(effect: float = EFFECT, drift: float = DRIFT, seed: int = 0) -> Cube:
    rng = np.random.default_rng(seed)
    days = calendar.day_range(date(2023, 1, 1), LAST_DAY)
    n_zones = N_TREATED + N_CONTROL
    base = rng.uniform(200, 2000, size=n_zones)
    values = np.zeros((len(days), n_zones, len(MEASURES)))
    for i, d in enumerate(days):
        cycle = np.sin(2 * np.pi * (d.month - 1) / 12)
        weekly = 1.1 if d.weekday() < 5 else 0.8
        months = (d.year - 2023) * 12 + d.month - 1
        for z in range(n_zones):
            treated = z < N_TREATED
            trips = base[z] * weekly * (1 + (0.3 if treated else 0.1) * cycle)
            trips *= 1 + rng.normal(0, 0.01)
            if treated:
                trips *= np.exp(drift * months)
                if d >= extended.FEE_START:
                    trips *= 1 + effect
            values[i, z] = [
                trips,
                0.75 * trips,
                0.25 * trips,
                20.0 * trips,
                25.0 * trips,
                900.0 * trips,
                3.0 * trips,
            ]
    return Cube(days=days, zones=tuple(range(1, n_zones + 1)), n_treated=N_TREATED, values=values)


@pytest.fixture(scope="module")
def result(p: dict[str, Any]) -> dict[str, Any]:
    b = p["bootstrap"]
    weights = bs.zone_weights(N_TREATED, N_CONTROL, int(b["rounds"]), int(b["seed"]))
    return extended.run_extended(make_cube(), p, weights)


def test_windows_are_whole_years_from_the_fee_day() -> None:
    assert extended.days_of("after")[0] == extended.FEE_START
    assert extended.days_of("placebo_after") == extended.days_of("before")
    assert len(extended.days_of("after")) == 361
    assert len(extended.days_of("before")) == 362  # 2024 is a leap year


def test_second_year_uses_the_same_days_of_each_year() -> None:
    before, after = extended.second_year_days(LAST_DAY)
    assert (before[0], before[-1]) == (date(2025, 1, 5), date(2025, 9, 30))
    assert (after[0], after[-1]) == (date(2026, 1, 5), date(2026, 9, 30))


def test_plain_estimate_carries_twelve_months_of_drift(result: dict[str, Any]) -> None:
    row = result["year_over_year"]["outcomes"][extended.PRIMARY]
    assert row["effect_log"] == pytest.approx(np.log1p(EFFECT) + 12 * DRIFT, abs=0.003)


def test_check_one_year_earlier_measures_the_drift_alone(result: dict[str, Any]) -> None:
    row = result["check_one_year_earlier"]["outcomes"][extended.PRIMARY]
    assert row["effect_log"] == pytest.approx(12 * DRIFT, abs=0.003)
    assert row["result"] == "fail"  # 4.7% is above the 3% line and the interval is tight


def test_triple_difference_finds_the_effect(result: dict[str, Any]) -> None:
    row = result["triple_difference"][extended.PRIMARY]
    assert row["effect"] == pytest.approx(np.log1p(EFFECT), abs=0.004)
    assert row["as_percent"] == pytest.approx(EFFECT, abs=0.004)
    assert row["interval_zone"][0] < row["effect"] < row["interval_zone"][1]
    assert row["interval_week"][0] < row["effect"] < row["interval_week"][1]
    assert row["se_zone"] > 0
    assert result["verdict"]["table_row"] == "leaves_out_zero"
    assert result["verdict"]["leads_with"] == "triple_difference"


def test_no_effect_gives_a_triple_difference_near_zero(p: dict[str, Any]) -> None:
    b = p["bootstrap"]
    weights = bs.zone_weights(N_TREATED, N_CONTROL, int(b["rounds"]), int(b["seed"]))
    out = extended.run_extended(make_cube(effect=0.0), p, weights)
    row = out["triple_difference"][extended.PRIMARY]
    assert abs(row["effect"]) < 0.004
    assert out["verdict"]["table_row"] == "holds_zero_inside_smallest_effect"


def test_second_fee_year_shows_only_the_drift(result: dict[str, Any]) -> None:
    row = result["second_fee_year"]["outcomes"][extended.PRIMARY]
    assert row["effect_log"] == pytest.approx(12 * DRIFT, abs=0.003)


def test_monthly_event_study_steps_down_in_2025_and_back_in_2026(result: dict[str, Any]) -> None:
    ev = result["event_study_monthly"]
    by_month = dict(zip(ev["months"], ev["change_from_a_year_earlier"], strict=True))
    assert ev["months"][0] == "2024-01" and ev["months"][-1] == "2026-09"
    assert by_month["2024-06"] == pytest.approx(12 * DRIFT, abs=0.01)
    assert by_month["2025-06"] == pytest.approx(np.log1p(EFFECT) + 12 * DRIFT, abs=0.01)
    assert by_month["2026-06"] == pytest.approx(12 * DRIFT, abs=0.01)
    assert ev["fee_in_month"][ev["months"].index("2025-01")] is True
    assert ev["fee_in_month_a_year_earlier"][ev["months"].index("2025-12")] is False
    assert ev["fee_in_month_a_year_earlier"][ev["months"].index("2026-01")] is True
    assert all(lo <= hi for lo, hi in zip(ev["band_low"], ev["band_high"], strict=True))


def test_zone_changes_split_treated_from_control(result: dict[str, Any]) -> None:
    rows = result["zone_changes"]
    treated = [r["change_vs_control_log"] for r in rows if r["group"] == "treated"]
    control = [r["change_vs_control_log"] for r in rows if r["group"] == "control"]
    assert len(treated) == N_TREATED and len(control) == N_CONTROL
    assert np.mean(treated) == pytest.approx(np.log1p(EFFECT) + 12 * DRIFT, abs=0.01)
    assert np.mean(control) == pytest.approx(0.0, abs=0.01)


def test_zone_with_no_trips_gets_no_change(p: dict[str, Any]) -> None:
    cube = make_cube()
    cube.values[:, 0, :] = 0.0
    rows = extended.zone_changes(cube, extended.days_of("before"), extended.days_of("after"))
    assert rows[0]["change_vs_control_log"] is None


def test_stop_rule_params_swap_the_windows_and_drop_the_holidays(p: dict[str, Any]) -> None:
    q = extended.stop_rule_params(p)
    assert q["windows"]["after"] == ["2025-01-05", "2025-12-31"]
    assert q["windows"]["pretrend_months"] == p["windows"]["pretrend_months"]
    assert q["excluded_dates"] == {"study": [], "placebo": []}
    assert p["windows"]["after"] == ["2025-01-05", "2025-03-31"]  # the plan is not changed


def test_mismatched_placebo_window_is_refused(
    p: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(extended.WINDOWS, "placebo_after", ("2024-01-06", "2024-12-31"))
    weights = bs.zone_weights(N_TREATED, N_CONTROL, 10, 1)
    with pytest.raises(ValueError, match="ADR 0009"):
        extended.run_extended(make_cube(), p, weights)
