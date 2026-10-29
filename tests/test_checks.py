"""Tests for the freshness, row-count and integrity check (ADR 0004 test 12, ADR 0007).

Each test loads tiny fake months with pipeline/synthetic.py under tmp_path, breaks one
thing, and checks that `nrc check` exits 1 with a message naming the test and month.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from nyc_rideshare_congestion import cli, config, params
from nyc_rideshare_congestion.pipeline import checks, db, dims, load, synthetic
from nyc_rideshare_congestion.pipeline.synthetic import full_month_rows, place_month, trip

NOV, DEC, JAN = "2024-11", "2024-12", "2025-01"
STOP_RULES = params.load_params()["stop_rules"]


def _plenty(_: Path) -> int:
    return 10**15


@pytest.fixture
def paths(tmp_path: Path) -> config.Paths:
    p = config.Paths(tmp_path)
    synthetic.write_zone_lookup(p.raw / config.ZONE_LOOKUP_FILE)
    return p


@pytest.fixture
def con(paths: config.Paths) -> Iterator[duckdb.DuckDBPyConnection]:
    c = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    dims.init_warehouse(c, paths.raw / config.ZONE_LOOKUP_FILE, [4, 12, 13])
    for month in (DEC, JAN):
        place_month(paths, c, month, full_month_rows(month, extra=20))
        load.load_month(month, paths, c, free_fn=_plenty)
    yield c
    c.close()


def _run(con: duckdb.DuckDBPyConnection, paths: config.Paths) -> list[str]:
    return [f.line() for f in checks.run_checks(con, paths, [DEC, JAN], STOP_RULES)]


def _has(lines: list[str], start: str) -> bool:
    return any(line.startswith(start) for line in lines)


def test_check_passes_on_a_complete_warehouse(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(con, paths) == []
    con.close()
    argv = ["--root", str(paths.root), "check", "--from", DEC, "--through", JAN]
    assert cli.main(argv) == config.EXIT_OK
    assert "check passed: 2 months" in capsys.readouterr().out


def test_missing_month_fails_loudly(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    con.close()
    argv = ["--root", str(paths.root), "check", "--from", DEC, "--through", "2025-02"]
    assert cli.main(argv) == config.EXIT_FAILED
    out = capsys.readouterr().out
    assert "FAIL freshness 2025-02: 0 loaded rows in load_log, expected 1" in out
    assert "FAIL freshness 2025-02: newest fact day 20250131 is not in the last month" in out
    assert "check failed:" in out


def test_default_range_fails_when_early_months_are_missing(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    con.close()
    assert cli.main(["--root", str(paths.root), "check"]) == config.EXIT_FAILED
    assert f"FAIL freshness {config.FIRST_MONTH}:" in capsys.readouterr().out


def test_from_after_through_is_a_bad_argument(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    con.close()
    argv = ["--root", str(paths.root), "check", "--from", JAN, "--through", DEC]
    assert cli.main(argv) == config.EXIT_BAD_ARGS
    with pytest.raises(SystemExit) as exc:
        cli.main(["--root", str(paths.root), "check", "--through", "2030-01"])
    assert exc.value.code == config.EXIT_BAD_ARGS


def test_counts_that_do_not_add_up_fail(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    con.execute("UPDATE load_log SET rows_kept = rows_kept + 1 WHERE source_month = ?", [DEC])
    con.execute("UPDATE load_log SET rows_quarantined = 5 WHERE source_month = ?", [JAN])
    lines = _run(con, paths)
    kept = con.execute("SELECT rows_kept FROM load_log WHERE source_month = ?", [DEC]).fetchone()
    assert kept is not None
    assert f"FAIL row_count {DEC}: fact trips {kept[0] - 1} != rows_kept {kept[0]}" in lines
    assert _has(lines, f"FAIL row_count {DEC}: other 0 + kept")
    assert f"FAIL row_count {JAN}: quarantine rows 0 != rows_quarantined 5" in lines


def test_orphan_zone_key_fails(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "INSERT INTO fact_trip_hourly VALUES (20241215, 3, 1, 999, 20, 1, 1, 60, 5, 6, 0, 0, 1)"
    )
    lines = _run(con, paths)
    assert "FAIL orphan_key pu_zone_key: 1 fact rows with no dim_zone row (first: 999)" in lines


def test_day_without_a_platform_fails(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DELETE FROM fact_trip_hourly WHERE date_key = 20241215 AND platform_key = 2")
    lines = _run(con, paths)
    assert (
        f"FAIL coverage {DEC}: 1 platform-days with fewer than 1 trips (first: 20241215 Lyft)"
        in lines
    )


def test_stale_raw_files_fail(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    dec = paths.raw / f"fhvhv_tripdata_{DEC}.parquet"
    jan = paths.raw / f"fhvhv_tripdata_{JAN}.parquet"
    synthetic.write_month(dec, full_month_rows(DEC, extra=99))
    jan.rename(jan.with_suffix(".moved"))
    lines = _run(con, paths)
    assert _has(lines, f"FAIL stale_file {DEC}: {dec.name} is ")
    assert f"FAIL stale_file {JAN}: {jan.name} is missing from {paths.raw}" in lines


def test_changed_modified_time_fails(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    con.execute("UPDATE load_log SET last_modified = TIMESTAMP '2025-02-01 12:00:00'")
    jan = paths.raw / f"fhvhv_tripdata_{JAN}.parquet"
    stamp = 1738411200.0  # 2025-02-01 12:00:00 UTC, the logged time
    os.utime(jan, (stamp, stamp))
    lines = _run(con, paths)
    assert _has(lines, f"FAIL stale_file {DEC}: ")
    assert not _has(lines, f"FAIL stale_file {JAN}")


def test_quarantine_share_over_the_plan_limit_fails(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    rows = full_month_rows(NOV) + [trip(NOV, 5000 + i, trip_miles=-1.0) for i in range(5)]
    place_month(paths, con, NOV, rows)
    load.load_month(NOV, paths, con, free_fn=_plenty)
    found = checks.run_checks(con, paths, [NOV, DEC, JAN], STOP_RULES)
    assert [f.scope for f in found if f.test == "quarantine_share"] == [NOV]
    assert all(f.test == "quarantine_share" for f in found)


def test_month_far_from_the_median_fails(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, NOV, full_month_rows(NOV, extra=400))
    load.load_month(NOV, paths, con, free_fn=_plenty)
    found = checks.run_checks(con, paths, [NOV, DEC, JAN], STOP_RULES)
    assert [(f.test, f.scope) for f in found] == [("row_count", NOV)]
    assert "outside" in found[0].detail and "median month" in found[0].detail


def test_repeated_primary_key_is_found() -> None:
    # A table without the primary key, so the repeat can exist.
    con = duckdb.connect()
    con.execute("CREATE TABLE dim_date (date_key INTEGER)")
    con.execute("CREATE TABLE dim_platform (platform_key TINYINT)")
    con.execute("CREATE TABLE dim_zone (zone_key SMALLINT)")
    con.execute("INSERT INTO dim_date VALUES (20241201)")
    con.execute("INSERT INTO dim_platform VALUES (1)")
    con.execute("INSERT INTO dim_zone VALUES (10), (20)")
    con.execute(
        "CREATE TABLE fact_trip_hourly AS SELECT * FROM (VALUES "
        "(20241201, 8, 1, 10, 20, 1), (20241201, 8, 1, 10, 20, 2), (20241201, 9, 1, 10, 20, 1)"
        ") AS t(date_key, pickup_hour, platform_key, pu_zone_key, do_zone_key, trip_count)"
    )
    found = checks.check_integrity(con)
    assert [f.line() for f in found] == [f"FAIL primary_key 202412: 1 repeated ({checks.PK}) keys"]


def test_empty_warehouse_reports_every_month_missing(tmp_path: Path) -> None:
    paths = config.Paths(tmp_path)
    con = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    db.run_schema(con)
    found = checks.run_checks(con, paths, [DEC, JAN], STOP_RULES)
    con.close()
    lines = [f.line() for f in found]
    assert f"FAIL freshness {DEC}: 0 loaded rows in load_log, expected 1" in lines
    assert f"FAIL freshness {JAN}: fact_trip_hourly is empty" in lines
