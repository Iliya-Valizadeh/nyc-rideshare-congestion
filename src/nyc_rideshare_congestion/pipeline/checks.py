"""The freshness, row-count and integrity check (ADR 0004 and 0007).

`run_checks` runs every test and returns one `Failure` per problem. The `check` command
prints them and exits 1 if there are any. It never runs on a timer.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import duckdb

from nyc_rideshare_congestion import config

PK = "date_key, pickup_hour, platform_key, pu_zone_key, do_zone_key"
# The five key columns packed into one HUGEINT with room for every SMALLINT zone key,
# so counting distinct keys is exact.
_PACKED_KEY = (
    "((((CAST(date_key AS HUGEINT) * 24 + pickup_hour) * 256 + platform_key)"
    " * 65536 + (pu_zone_key + 32768)) * 65536 + (do_zone_key + 32768))"
)


@dataclass(frozen=True)
class Failure:
    test: str
    scope: str
    detail: str

    def line(self) -> str:
        return f"FAIL {self.test} {self.scope}: {self.detail}"


@dataclass(frozen=True)
class LoadedMonth:
    month: str
    file_name: str
    size_bytes: int
    last_modified: datetime | None
    rows_in_file: int
    rows_other_platform: int
    rows_kept: int
    rows_quarantined: int


def _rows(con: duckdb.DuckDBPyConnection, sql: str, args: list[Any] | None = None) -> list[Any]:
    return con.execute(sql, args or []).fetchall()


def _month_key(month: str) -> int:
    return int(month.replace("-", ""))


def loaded_months(con: duckdb.DuckDBPyConnection) -> dict[str, list[LoadedMonth]]:
    """Every `loaded` row in load_log, grouped by month."""
    out: dict[str, list[LoadedMonth]] = {}
    for r in _rows(
        con,
        "SELECT source_month, file_name, size_bytes, last_modified, rows_in_file, "
        "rows_other_platform, rows_kept, rows_quarantined FROM load_log "
        "WHERE status = 'loaded' ORDER BY source_month, load_id",
    ):
        nums = [int(x or 0) for x in r[4:]]
        out.setdefault(r[0], []).append(LoadedMonth(r[0], r[1], int(r[2]), r[3], *nums))
    return out


def check_freshness(
    con: duckdb.DuckDBPyConnection,
    paths: config.Paths,
    expected: list[str],
    loaded: dict[str, list[LoadedMonth]],
) -> list[Failure]:
    """Each expected month loaded once, the newest fact day in the last expected month,
    and each raw file unchanged since download (size and modified time, no hash)."""
    fails = []
    for month in expected:
        n = len(loaded.get(month, []))
        if n != 1:
            fails.append(Failure("freshness", month, f"{n} loaded rows in load_log, expected 1"))
    newest = _rows(con, "SELECT max(date_key) FROM fact_trip_hourly")[0][0]
    last = expected[-1]
    if newest is None:
        fails.append(Failure("freshness", last, "fact_trip_hourly is empty"))
    elif int(newest) // 100 != _month_key(last):
        fails.append(
            Failure("freshness", last, f"newest fact day {newest} is not in the last month {last}")
        )
    for month in expected:
        for row in loaded.get(month, []):
            fails += _stale(paths, row)
    return fails


def _stale(paths: config.Paths, row: LoadedMonth) -> list[Failure]:
    path = paths.raw / row.file_name
    if not path.exists():
        return [Failure("stale_file", row.month, f"{path.name} is missing from {paths.raw}")]
    stat = path.stat()
    if stat.st_size != row.size_bytes:
        return [
            Failure(
                "stale_file",
                row.month,
                f"{path.name} is {stat.st_size} bytes, load_log says {row.size_bytes}",
            )
        ]
    if row.last_modified is not None:
        logged = row.last_modified.replace(tzinfo=UTC).timestamp()
        if abs(stat.st_mtime - logged) > config.MTIME_TOLERANCE_S:
            on_disk = datetime.fromtimestamp(stat.st_mtime, UTC).strftime("%Y-%m-%d %H:%M:%S")
            return [
                Failure(
                    "stale_file",
                    row.month,
                    f"{path.name} modified {on_disk} UTC, load_log says {row.last_modified}",
                )
            ]
    return []


def check_row_counts(
    con: duckdb.DuckDBPyConnection,
    expected: list[str],
    loaded: dict[str, list[LoadedMonth]],
    stop_rules: dict[str, Any],
) -> list[Failure]:
    """The counts in load_log add up and match the fact and quarantine tables, every day
    has trips for each platform, and no month is far from the median month."""
    rows = [loaded[m][0] for m in expected if len(loaded.get(m, [])) == 1]
    fact = dict(
        _rows(
            con,
            "SELECT date_key // 100, sum(trip_count) FROM fact_trip_hourly GROUP BY 1",
        )
    )
    quarantined = dict(_rows(con, "SELECT source_month, count(*) FROM quarantine_trip GROUP BY 1"))
    max_share = float(stop_rules["max_quarantine_share"])
    fails = []
    for r in rows:
        parts = r.rows_other_platform + r.rows_kept + r.rows_quarantined
        if parts != r.rows_in_file:
            fails.append(
                Failure(
                    "row_count",
                    r.month,
                    f"other {r.rows_other_platform} + kept {r.rows_kept} + quarantined "
                    f"{r.rows_quarantined} = {parts} != rows in file {r.rows_in_file}",
                )
            )
        trips = int(fact.get(_month_key(r.month), 0))
        if trips != r.rows_kept:
            fails.append(
                Failure("row_count", r.month, f"fact trips {trips} != rows_kept {r.rows_kept}")
            )
        q = int(quarantined.get(r.month, 0))
        if q != r.rows_quarantined:
            fails.append(
                Failure(
                    "row_count",
                    r.month,
                    f"quarantine rows {q} != rows_quarantined {r.rows_quarantined}",
                )
            )
        in_scope = r.rows_kept + r.rows_quarantined
        share = r.rows_quarantined / in_scope if in_scope else 0.0
        if share > max_share:
            fails.append(
                Failure(
                    "quarantine_share",
                    r.month,
                    f"{share:.5f} of Uber and Lyft rows quarantined > {max_share}",
                )
            )
    if rows:
        median = statistics.median(r.rows_kept for r in rows)
        low, high = (median * k for k in config.ROWS_KEPT_BAND)
        fails += [
            Failure(
                "row_count",
                r.month,
                f"rows_kept {r.rows_kept} outside {low:.0f} to {high:.0f} "
                f"(median month {median:.0f})",
            )
            for r in rows
            if not low <= r.rows_kept <= high
        ]
    fails += _coverage(
        con, [r.month for r in rows], int(stop_rules["min_rows_per_platform_per_day"])
    )
    return fails


def _coverage(con: duckdb.DuckDBPyConnection, months: list[str], min_trips: int) -> list[Failure]:
    """Every day of each loaded month has at least min_trips trips for each platform."""
    if not months:
        return []
    keys = [_month_key(m) for m in months]
    short = _rows(
        con,
        """
        WITH days AS (
            SELECT d.date_key, d.month_key, p.platform_key, p.platform_name
            FROM dim_date AS d CROSS JOIN dim_platform AS p
            WHERE list_contains(?, d.month_key)
        ),
        trips AS (
            SELECT date_key, platform_key, sum(trip_count) AS trips
            FROM fact_trip_hourly GROUP BY 1, 2
        )
        SELECT d.month_key, count(*), min(d.date_key || ' ' || d.platform_name)
        FROM days AS d
        LEFT JOIN trips AS t USING (date_key, platform_key)
        WHERE coalesce(t.trips, 0) < ?
        GROUP BY 1 ORDER BY 1
        """,
        [keys, min_trips],
    )
    return [
        Failure(
            "coverage",
            f"{str(k)[:4]}-{str(k)[4:]}",
            f"{n} platform-days with fewer than {min_trips} trips (first: {first})",
        )
        for k, n, first in short
    ]


def check_integrity(con: duckdb.DuckDBPyConnection) -> list[Failure]:
    """No fact key missing from its dimension, and no repeated fact primary key."""
    refs = (
        ("date_key", "dim_date", "date_key"),
        ("platform_key", "dim_platform", "platform_key"),
        ("pu_zone_key", "dim_zone", "zone_key"),
        ("do_zone_key", "dim_zone", "zone_key"),
    )
    fails = []
    for col, dim, key in refs:
        n, example = _rows(
            con,
            f"SELECT count(*), min(f.{col}) FROM fact_trip_hourly AS f "
            f"ANTI JOIN {dim} AS d ON d.{key} = f.{col}",
        )[0]
        if n:
            fails.append(
                Failure("orphan_key", col, f"{n} fact rows with no {dim} row (first: {example})")
            )
    # One month at a time keeps the distinct count inside the memory cap.
    for (month_key,) in _rows(
        con, "SELECT DISTINCT date_key // 100 FROM fact_trip_hourly ORDER BY 1"
    ):
        lo, hi = month_key * 100, month_key * 100 + 99
        repeats = _rows(
            con,
            f"SELECT count(*) - count(DISTINCT {_PACKED_KEY}) FROM fact_trip_hourly "
            "WHERE date_key BETWEEN ? AND ?",
            [lo, hi],
        )[0][0]
        if repeats:
            fails.append(Failure("primary_key", str(month_key), f"{repeats} repeated ({PK}) keys"))
    return fails


def run_checks(
    con: duckdb.DuckDBPyConnection,
    paths: config.Paths,
    expected: list[str],
    stop_rules: dict[str, Any],
) -> list[Failure]:
    """Run every test for the expected months. An empty list means all passed."""
    loaded = loaded_months(con)
    return (
        check_freshness(con, paths, expected, loaded)
        + check_row_counts(con, expected, loaded, stop_rules)
        + check_integrity(con)
    )
