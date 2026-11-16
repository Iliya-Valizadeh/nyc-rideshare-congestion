"""The plan's outcomes and the two ways to compare groups ("Outcomes", "Estimator").

Every function works on sums whose last axis is panel.MEASURES, with any leading axes
(bootstrap rounds, months), so the full-data value and each bootstrap round use the
same code.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .panel import Array, M


@dataclass(frozen=True)
class Outcome:
    name: str
    kind: str  # "ratio": counts per day, compared as ratios; "level": compared as differences
    numerator: str
    denominator: str | None = None
    scale: float = 1.0
    unit: str = ""
    primary: bool = False


OUTCOMES = (
    Outcome("trips_per_day_both_platforms", "ratio", "trips", unit="trips per day", primary=True),
    Outcome("trips_per_day_uber", "ratio", "trips_uber", unit="trips per day"),
    Outcome("trips_per_day_lyft", "ratio", "trips_lyft", unit="trips per day"),
    Outcome("base_fare_per_trip", "level", "base_fare_sum", "trips", unit="dollars"),
    Outcome("rider_price_per_trip", "level", "rider_price_sum", "trips", unit="dollars"),
    Outcome("minutes_per_trip", "level", "trip_time_sum_s", "trips", 1 / 60, "minutes"),
    Outcome("minutes_per_mile", "level", "trip_time_sum_s", "trip_miles_sum", 1 / 60, "minutes"),
    Outcome("lyft_share", "level", "trips_lyft", "trips", 100.0, "percentage points"),
)
BY_NAME = {o.name: o for o in OUTCOMES}


def group_value(o: Outcome, sums: Array, n_days: Array | float) -> Array:
    """One group's outcome: mean per day for counts, a trip-weighted mean otherwise."""
    if o.kind == "ratio":
        return np.asarray(sums[..., M[o.numerator]] / n_days)
    assert o.denominator is not None
    return np.asarray(sums[..., M[o.numerator]] / sums[..., M[o.denominator]] * o.scale)


def gap(o: Outcome, treated: Array, control: Array) -> Array:
    """Treated against control in one period: log ratio for counts, difference otherwise."""
    if o.kind == "ratio":
        return np.asarray(np.log(treated / control))
    return np.asarray(treated - control)


def did(o: Outcome, t_before: Array, t_after: Array, c_before: Array, c_after: Array) -> Array:
    """The plan's 2x2 estimate: a ratio of ratios minus one, or a difference of differences."""
    if o.kind == "ratio":
        return np.asarray((t_after / t_before) / (c_after / c_before) - 1.0)
    return np.asarray((t_after - t_before) - (c_after - c_before))
