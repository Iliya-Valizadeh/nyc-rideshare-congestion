"""Build the analysis panel in the warehouse and read it back as arrays.

The panel has one cell per drop-off zone, day and platform (docs/eval_plan.md, "Unit of
analysis"). It is built one month at a time inside the DuckDB memory cap, and each month
is deleted and written again, so a re-run gives the same table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb
import numpy as np
import numpy.typing as npt

from nyc_rideshare_congestion import config

from .zones import ZoneGroups

SQL = config.SQL_DIR / "analysis"

# The measures each cell carries, in the order of the cube's last axis.
MEASURES = (
    "trips",
    "trips_uber",
    "trips_lyft",
    "base_fare_sum",
    "rider_price_sum",
    "trip_time_sum_s",
    "trip_miles_sum",
)
M = {name: i for i, name in enumerate(MEASURES)}

Array = npt.NDArray[np.float64]


class PanelError(RuntimeError):
    """The panel does not add up to the fact table."""


def _month_key(month: str) -> int:
    return int(month.replace("-", ""))


def _params(groups: ZoneGroups, month: str) -> dict[str, Any]:
    key = _month_key(month)
    return {
        "lo": key * 100,
        "hi": key * 100 + 99,
        "treated": list(groups.treated),
        "control": list(groups.control),
        "excluded_pickup": list(groups.excluded_pickup),
        "side_a": list(groups.side_a),
        "side_b": list(groups.side_b),
    }


def _trips_sql() -> str:
    return (SQL / "02_panel_trips.sql").read_text(encoding="utf-8")


def build_month(con: duckdb.DuckDBPyConnection, groups: ZoneGroups, month: str) -> dict[str, Any]:
    """Write one month of the panel and check its trip total against the fact table."""
    key = _month_key(month)
    con.execute((SQL / "01_panel_table.sql").read_text(encoding="utf-8"))
    con.execute("DELETE FROM panel_zone_day WHERE date_key // 100 = ?", [key])
    insert = (SQL / "03_panel_insert.sql").read_text(encoding="utf-8")
    insert = insert.replace("{trips_sql}", _trips_sql())
    con.execute(insert, {**_params(groups, month), "month_key": key})
    panel_trips, cells = con.execute(
        "SELECT coalesce(sum(trips), 0), count(*) FROM panel_zone_day WHERE date_key // 100 = ?",
        [key],
    ).fetchone() or (0, 0)
    # The same rows, summed straight from the fact table with no cell grid in between.
    fact_trips = con.execute(
        f"SELECT coalesce(sum(trips), 0) FROM ({_trips_sql()})", _params(groups, month)
    ).fetchone()
    fact_total = int(fact_trips[0]) if fact_trips else 0
    row = {
        "month": month,
        "cells": int(cells),
        "panel_trips": int(panel_trips),
        "fact_trips": fact_total,
        "equal": int(panel_trips) == fact_total,
    }
    if not row["equal"]:
        raise PanelError(f"{month}: panel trips {panel_trips} != fact trips {fact_total}")
    return row


@dataclass(frozen=True)
class Cube:
    """Panel sums as a (day, zone, measure) array. Zones: treated first, then control."""

    days: list[date]
    zones: tuple[int, ...]
    n_treated: int
    values: Array

    @property
    def treated(self) -> Array:
        return self.values[:, : self.n_treated, :]

    @property
    def control(self) -> Array:
        return self.values[:, self.n_treated :, :]

    def day_index(self, days: list[date]) -> npt.NDArray[np.intp]:
        where = {d: i for i, d in enumerate(self.days)}
        missing = [d for d in days if d not in where]
        if missing:
            raise PanelError(f"the panel has no cells for {missing[0]} ({len(missing)} days)")
        return np.array([where[d] for d in days], dtype=np.intp)


def load_cube(con: duckdb.DuckDBPyConnection, groups: ZoneGroups, first: date, last: date) -> Cube:
    """Read the panel for first..last, both platforms summed but kept apart for counts."""
    rows = con.execute(
        """
        SELECT
            c.full_date,
            c.do_zone_key,
            sum(c.trips),
            sum(c.trips) FILTER (WHERE p.license_num = 'HV0003'),
            sum(c.trips) FILTER (WHERE p.license_num = 'HV0005'),
            sum(c.base_fare_sum),
            sum(c.rider_price_sum),
            sum(c.trip_time_sum_s),
            sum(c.trip_miles_sum)
        FROM panel_zone_day AS c
        JOIN dim_platform AS p USING (platform_key)
        WHERE c.full_date BETWEEN ? AND ?
        GROUP BY 1, 2
        """,
        [first, last],
    ).fetchall()
    zones = groups.panel_zones
    zpos = {z: i for i, z in enumerate(zones)}
    days = sorted({r[0] for r in rows})
    dpos = {d: i for i, d in enumerate(days)}
    values = np.zeros((len(days), len(zones), len(MEASURES)))
    for r in rows:
        values[dpos[r[0]], zpos[int(r[1])], :] = [float(v or 0) for v in r[2:]]
    return Cube(days=days, zones=zones, n_treated=len(groups.treated), values=values)
