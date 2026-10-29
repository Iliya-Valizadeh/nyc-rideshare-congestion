"""Tests for the SQL transforms and the index timing (ADR 0007).

Fact rows are written by hand into a fresh warehouse under tmp_path, so every expected
value can be worked out on paper. No real trips, no network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import duckdb
import pytest

from nyc_rideshare_congestion import cli, config
from nyc_rideshare_congestion.pipeline import db, dims, index_timing, synthetic, transforms

# In the fake lookup, zone i is in BOROUGHS[i % 5]: 10, 15, 20, 25 are Manhattan and 11 is
# Brooklyn. Platform 1 is Uber, 2 is Lyft.
UBER, LYFT = 1, 2


@pytest.fixture
def paths(tmp_path: Path) -> config.Paths:
    p = config.Paths(tmp_path)
    synthetic.write_zone_lookup(p.raw / config.ZONE_LOOKUP_FILE)
    return p


@pytest.fixture
def con(paths: config.Paths) -> Iterator[duckdb.DuckDBPyConnection]:
    c = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    dims.init_warehouse(c, paths.raw / config.ZONE_LOOKUP_FILE, [4, 12, 13])
    yield c
    c.close()


def _fact(
    con: duckdb.DuckDBPyConnection,
    date_key: int,
    hour: int,
    platform: int,
    pu: int,
    trips: int,
    *,
    do: int = 20,
    fare: float | None = None,
    miles: float | None = None,
) -> None:
    fare = trips * 10.0 if fare is None else fare
    miles = trips * 2.0 if miles is None else miles
    con.execute(
        "INSERT INTO fact_trip_hourly VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 1)",
        [date_key, hour, platform, pu, do, trips, miles, trips * 600, fare, fare + trips],
    )


@pytest.fixture
def built(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    _fact(con, 20241201, 8, UBER, 10, 3)
    _fact(con, 20241201, 8, LYFT, 10, 1)
    _fact(con, 20241208, 8, UBER, 10, 4)
    _fact(con, 20241201, 9, UBER, 10, 4)
    _fact(con, 20241210, 9, UBER, 15, 3, fare=30.0, miles=3.0)
    _fact(con, 20241211, 9, UBER, 25, 1, fare=12.0, miles=0.0)
    _fact(con, 20250102, 8, UBER, 10, 2, fare=20.0, miles=2.0)
    _fact(con, 20250105, 12, LYFT, 11, 5)
    _fact(con, 20250301, 8, UBER, 10, 1, fare=10.0, miles=1.0)
    transforms.run_transforms(con)
    return con


def _one(con: duckdb.DuckDBPyConnection, sql: str) -> tuple[object, ...]:
    row = con.execute(sql).fetchone()
    assert row is not None
    return tuple(row)


def test_hourly_demand_fills_every_hour_and_windows_by_calendar_day(
    built: duckdb.DuckDBPyConnection,
) -> None:
    days = _one(built, "SELECT count(*) FROM dim_date WHERE date_key BETWEEN 20241201 AND 20250301")
    boroughs = _one(built, "SELECT count(DISTINCT borough) FROM dim_zone")
    total = _one(built, "SELECT count(*), sum(trips) FROM tf_hourly_demand")
    assert total == (int(str(days[0])) * 24 * int(str(boroughs[0])), 24)

    sql = (
        "SELECT trips, trips_same_hour_7d_avg, trips_same_hour_last_week, share_of_day, "
        "hour_rank_in_day FROM tf_hourly_demand WHERE borough = 'Manhattan' "
        "AND pickup_hour = {h} AND date_key = {d}"
    )
    # Two platforms add up; hours 8 and 9 tie for first place on Dec 1.
    assert _one(built, sql.format(h=8, d=20241201)) == (4, 4.0, None, 0.5, 1)
    assert _one(built, sql.format(h=9, d=20241201))[4] == 1
    assert _one(built, sql.format(h=0, d=20241201))[3:] == (0.0, 3)
    # Dec 8: the seven-day frame is Dec 2 to Dec 8, and last week is Dec 1.
    assert _one(built, sql.format(h=8, d=20241208))[:3] == (4, pytest.approx(4 / 7), 4)
    assert _one(built, sql.format(h=8, d=20241207))[:3] == (0, pytest.approx(4 / 7), None)
    # A day with no trips in the borough has no share, not a division error.
    assert _one(built, sql.format(h=8, d=20241203))[3] is None


def test_platform_share_adds_up_and_fills_missing_platforms(
    built: duckdb.DuckDBPyConnection,
) -> None:
    rows = built.execute(
        "SELECT month_key, borough, platform_name, trips, trip_share, share_change_vs_prev_month "
        "FROM tf_platform_share ORDER BY month_key, borough, platform_key"
    ).fetchall()
    got = {(m, b, p): (t, s, c) for m, b, p, t, s, c in rows}
    # Manhattan in December: Uber 3 + 4 + 4 in zone 10, 3 in zone 15, 1 in zone 25.
    assert got[(202412, "Manhattan", "Uber")] == (15, pytest.approx(15 / 16), None)
    assert got[(202412, "Manhattan", "Lyft")] == (1, pytest.approx(1 / 16), None)
    assert got[(202501, "Manhattan", "Uber")] == (2, 1.0, pytest.approx(1 / 16))
    assert got[(202501, "Manhattan", "Lyft")] == (0, 0.0, pytest.approx(-1 / 16))
    assert got[(202501, "Brooklyn", "Uber")] == (0, 0.0, None)
    assert got[(202501, "Brooklyn", "Lyft")] == (5, 1.0, None)
    sums = built.execute(
        "SELECT sum(trip_share) FROM tf_platform_share GROUP BY month_key, borough"
    ).fetchall()
    assert all(s == pytest.approx(1.0) for (s,) in sums)


def test_fare_per_mile_ranks_and_rolls_by_calendar_month(
    built: duckdb.DuckDBPyConnection,
) -> None:
    sql = (
        "SELECT trips, fare_per_mile, fare_per_mile_3m, rank_in_borough "
        "FROM tf_fare_per_mile_by_zone WHERE platform_key = 1 "
        "AND pu_zone_key = {z} AND month_key = {m}"
    )
    # Zone 10 in December: fares 30 + 40 + 40 over miles 6 + 8 + 8.
    assert _one(built, sql.format(z=10, m=202412)) == (11, 5.0, 5.0, 2)
    assert _one(built, sql.format(z=15, m=202412)) == (3, 10.0, 10.0, 1)
    # Zero miles: no ratio and no rank.
    assert _one(built, sql.format(z=25, m=202412))[1:] == (None, None, None)
    # January's three-month window holds December and January.
    assert _one(built, sql.format(z=10, m=202501))[1:3] == (10.0, pytest.approx(130 / 24))
    # March's window is January to March by calendar month, so December drops out.
    assert _one(built, sql.format(z=10, m=202503))[1:3] == (10.0, pytest.approx(10.0))


def test_transforms_are_safe_to_run_twice(built: duckdb.DuckDBPyConnection) -> None:
    first = transforms.run_transforms(built)
    assert transforms.run_transforms(built) == first
    assert set(first) == {"tf_hourly_demand", "tf_platform_share", "tf_fare_per_mile_by_zone"}


def test_transform_refuses_an_empty_fact_table(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(transforms.TransformError, match="empty"):
        transforms.run_transforms(con)


def test_table_name_needs_a_create_statement(tmp_path: Path) -> None:
    bad = tmp_path / "bad.sql"
    bad.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="no CREATE"):
        transforms.table_name(bad)


def test_transform_and_time_index_commands(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    root = ["--root", str(paths.root)]
    con.close()
    assert cli.main([*root, "transform"]) == config.EXIT_FAILED
    assert "fact_trip_hourly is empty" in capsys.readouterr().out
    assert cli.main([*root, "time-index"]) == config.EXIT_FAILED
    assert "nothing to time" in capsys.readouterr().out

    again = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    _fact(again, 20241201, 8, UBER, 10, 3)
    for do in (21, 22, 23):
        _fact(again, 20241201, 9, UBER, 10, 1, do=do)
    again.close()
    assert cli.main([*root, "transform"]) == config.EXIT_OK
    assert "transform tf_platform_share: 2 rows" in capsys.readouterr().out
    assert cli.main([*root, "time-index"]) == config.EXIT_OK
    out = capsys.readouterr().out
    assert "full table, 4 rows: index built" in out
    assert "index used: True" in out
    report = json.loads((paths.reports / config.INDEX_TIMING_REPORT).read_text(encoding="utf-8"))
    assert report["command"] == "uv run nrc time-index"


def _three_zones(con: duckdb.DuckDBPyConnection) -> None:
    """Drop-off zone 20 has 5 rows, 21 has 3 and 22 has 1, across two months."""
    for day in range(1, 6):
        _fact(con, 20241200 + day, 8, UBER, 10, 1, do=20)
    for day in range(1, 4):
        _fact(con, 20250100 + day, 8, UBER, 10, 1, do=21)
    _fact(con, 20250101, 9, UBER, 10, 1, do=22)


def test_index_timing_picks_zones_by_rule_and_leaves_the_index(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    _three_zones(con)
    assert index_timing.pick_zones(con) == {
        "most_rows": (20, 5),
        "median_rows": (21, 3),
        "fewest_rows": (22, 1),
    }
    scratch = tmp_path / "tmp" / "scratch.duckdb"
    result = index_timing.run_timing(con, scratch, runs=2, slice_months=1)
    assert index_timing.has_index(con)
    assert not scratch.exists()
    assert "single-machine" in result["note"].lower()
    assert result["machine"]["duckdb_threads"] == str(config.DUCKDB_THREADS)
    full = result["full_table"]
    assert full["fact_rows"] == 9
    assert full["index_build"]["status"] == "built"
    for zone in full["zones"]:
        assert zone["before"]["uses_index"] is False
        assert zone["before"]["runs"] == 2
        assert zone["after"]["runs"] == 2
    part = result["slice"]
    # The newest month only: zones 21 and 22.
    assert part["months"] == ["2025-01"]
    assert part["fact_rows"] == 4
    assert part["index_build"]["status"] == "built"
    assert {z["do_zone_key"] for z in part["zones"]} == {21, 22}
    used = {z["label"]: z["after_index"]["uses_index"] for z in part["zones"]}
    # DuckDB uses the index when a filter matches at most index_scan_max_count rows, so
    # on a table this small every zone qualifies.
    assert used == {"most_rows": True, "median_rows": True, "fewest_rows": True}
    for zone in part["zones"]:
        assert zone["before"]["uses_index"] is False
        assert zone["sorted_by_do_zone"]["uses_index"] is False
    # The warehouse is the default database again, and the scratch one is gone.
    assert _one(con, "SELECT current_database()") == ("warehouse",)
    # Running it again drops the index first, so "before" is still without it.
    again = index_timing.run_timing(con, scratch, runs=1, slice_months=1)
    assert all(z["before"]["uses_index"] is False for z in again["full_table"]["zones"])
    path = index_timing.write_report(result, tmp_path / "reports")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["full_table"]["zones"][0]["do_zone_key"] == 20


def test_index_that_runs_out_of_memory_is_recorded(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _three_zones(con)
    calls: list[str] = []
    real = index_timing.build_index

    def full_table_runs_out(c: duckdb.DuckDBPyConnection) -> dict[str, object]:
        calls.append(str(_one(c, "SELECT current_database()")[0]))
        if len(calls) == 1:
            return {"status": "out_of_memory", "seconds": 0.0, "error": "Out of Memory Error"}
        return real(c)

    monkeypatch.setattr(index_timing, "build_index", full_table_runs_out)
    result = index_timing.run_timing(con, tmp_path / "s.duckdb", runs=1, slice_months=2)
    assert calls == ["warehouse", index_timing.SCRATCH_ALIAS]
    full = result["full_table"]
    assert full["index_build"]["status"] == "out_of_memory"
    assert all(z["after"] is None and z["speedup"] is None for z in full["zones"])
    assert not index_timing.has_index(con)
    assert result["slice"]["months"] == ["2024-12", "2025-01"]
    assert result["slice"]["index_build"]["status"] == "built"


class _NoMemory:
    def execute(self, sql: str) -> None:
        raise duckdb.OutOfMemoryException(
            "Out of Memory Error: failed to allocate" + chr(10) + "more"
        )


def test_build_index_catches_only_out_of_memory(con: duckdb.DuckDBPyConnection) -> None:
    got = index_timing.build_index(cast(duckdb.DuckDBPyConnection, _NoMemory()))
    assert got["status"] == "out_of_memory"
    assert got["error"] == "Out of Memory Error: failed to allocate"
    con.execute("DROP TABLE fact_trip_hourly")
    with pytest.raises(duckdb.CatalogException):
        index_timing.build_index(con)


def test_slice_timing_cleans_up_after_an_error(
    con: duckdb.DuckDBPyConnection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _three_zones(con)

    def boom(*args: object) -> dict[str, object]:
        raise RuntimeError("boom")

    monkeypatch.setattr(index_timing, "time_zones", boom)
    scratch = tmp_path / "s.duckdb"
    with pytest.raises(RuntimeError, match="boom"):
        index_timing.time_slice(con, scratch, runs=1, n_months=1)
    assert not scratch.exists()
    assert _one(con, "SELECT current_database()") == ("warehouse",)


def test_total_ram_is_reported() -> None:
    ram = index_timing.total_ram_bytes()
    assert ram is None or ram > 0
