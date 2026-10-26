"""Tests for validation, quarantine and the star-schema load (ADR 0004 tests 1 to 11, 14, 15).

Every test writes its own tiny fake Parquet files under tmp_path with
pipeline/synthetic.py. No real trips, no network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nyc_rideshare_congestion import cli, config, params
from nyc_rideshare_congestion.pipeline import db, dims, load, rules, schema, summary, synthetic
from nyc_rideshare_congestion.pipeline.synthetic import bad_rows, place_month, trip

DEC, JAN = "2024-12", "2025-01"
CBD = [4, 12, 13]
TABLES = ("fact_trip_hourly", "quarantine_trip", "load_log")


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
    dims.init_warehouse(c, paths.raw / config.ZONE_LOOKUP_FILE, CBD)
    yield c
    c.close()


def _load(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, month: str, **kw: bool
) -> load.LoadResult:
    return load.load_month(month, paths, con, free_fn=_plenty, **kw)


def _count(con: duckdb.DuckDBPyConnection, sql: str, args: list[object] | None = None) -> int:
    row = con.execute(sql, args or []).fetchone()
    assert row is not None
    return 0 if row[0] is None else int(row[0])


def _sizes(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {t: _count(con, f"SELECT count(*) FROM {t}") for t in TABLES}


def _codes(con: duckdb.DuckDBPyConnection) -> dict[int, str]:
    rows = con.execute("SELECT src_row, reason_code FROM quarantine_trip").fetchall()
    return {int(r): str(c) for r, c in rows}


def _log(con: duckdb.DuckDBPyConnection, month: str) -> tuple[object, ...]:
    row = con.execute(
        "SELECT rows_in_file, rows_other_platform, rows_kept, rows_quarantined, "
        "rows_fee_part_null, fact_rows, status FROM load_log "
        "WHERE source_month = ? AND status = 'loaded'",
        [month],
    ).fetchone()
    assert row is not None
    return tuple(row)


# 1. Malformed rows.
def test_each_rule_quarantines_its_row_and_counts_add_up(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    bad = bad_rows(JAN)
    good = [trip(JAN, i) for i in range(3)]
    place_month(paths, con, JAN, good + list(bad.values()))
    result = _load(paths, con, JAN)
    assert result.action == "loaded"
    expected = {len(good) + k: code for k, code in enumerate(bad)}
    assert _codes(con) == expected
    assert result.rows_kept == len(good)
    assert result.rows_quarantined == len(bad)
    assert result.rows_kept + result.rows_quarantined + result.rows_other_platform == len(
        good + list(bad.values())
    )
    details = dict(con.execute("SELECT reason_code, reason_detail FROM quarantine_trip").fetchall())
    assert details["NULL_REQUIRED"] == "DOLocationID"
    assert details["UNKNOWN_ZONE"] == "PULocationID"
    assert details["NEGATIVE_FARE"] == "tolls"
    assert details["NEGATIVE_DISTANCE"] is None
    assert _count(con, "SELECT sum(trip_count) FROM fact_trip_hourly") == len(good)
    # The quarantine keeps the bad value for a reviewer.
    assert _count(con, "SELECT tolls FROM quarantine_trip WHERE reason_code='NEGATIVE_FARE'") == -1


@pytest.mark.parametrize(
    "override",
    [
        {"trip_time": -5},
        {"trip_time": config.MAX_TRIP_SECONDS + 1},
        {"dropoff_datetime": synthetic.month_start(JAN)},
    ],
)
def test_impossible_duration_variants(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, override: dict[str, object]
) -> None:
    place_month(paths, con, JAN, [trip(JAN, 0), trip(JAN, 1, **override)])
    _load(paths, con, JAN)
    assert _codes(con) == {1: "IMPOSSIBLE_DURATION"}


def test_zero_fare_and_unknown_dropoff_zone_rows_are_kept(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    rows = [trip(JAN, 0, base_passenger_fare=0.0), trip(JAN, 1, DOLocationID=264)]
    place_month(paths, con, JAN, rows)
    assert _load(paths, con, JAN).rows_kept == 2


# 2. Missing column.
def test_missing_column_exits_2_and_writes_nothing(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, capsys: pytest.CaptureFixture[str]
) -> None:
    place_month(paths, con, JAN, [trip(JAN, 0)], drop=("DOLocationID",))
    before = _sizes(con)
    con.close()
    code = cli.main(["--root", str(paths.root), "load", "--month", JAN], free_fn=_plenty)
    assert code == config.EXIT_SCHEMA
    assert "SCHEMA_MISSING_COLUMN" in capsys.readouterr().out
    after = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    try:
        assert _sizes(after) == before
        assert _count(after, "SELECT count(*) FROM load_log WHERE status = 'loaded'") == 0
    finally:
        after.close()


def test_bad_type_is_a_schema_error(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    rows = [trip(JAN, 0, trip_miles=None)]
    path = synthetic.write_month(paths.raw / "x.parquet", rows)
    table = pq.read_table(path).set_column(9, "trip_miles", pa.array(["2.5"], type=pa.string()))
    pq.write_table(table, path)
    with pytest.raises(schema.SchemaError, match="SCHEMA_BAD_TYPE"):
        schema.check_file(path, JAN)


# 3. Duplicate events.
def test_exact_copies_keep_one_and_near_copies_keep_both(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    first = trip(JAN, 0)
    near = {**trip(JAN, 1), "tips": 9.0}
    rows = [first, trip(JAN, 1), dict(first), near, dict(first)]
    place_month(paths, con, JAN, rows)
    result = _load(paths, con, JAN)
    assert _codes(con) == {2: "DUPLICATE_TRIP", 4: "DUPLICATE_TRIP"}
    assert result.rows_kept == 3
    assert result.reasons["DUPLICATE_TRIP"] == 2


def test_copies_of_a_bad_row_keep_the_earlier_code(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    bad = trip(JAN, 0, trip_miles=-1.0)
    place_month(paths, con, JAN, [bad, dict(bad)])
    _load(paths, con, JAN)
    assert set(_codes(con).values()) == {"NEGATIVE_DISTANCE"}


def test_null_columns_count_in_duplicate_detection(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    a = trip(JAN, 0, tolls=None)
    b = trip(JAN, 0, tolls=0.0)  # same as a except null vs zero: a near copy
    place_month(paths, con, JAN, [a, b, dict(a)])
    _load(paths, con, JAN)
    assert _codes(con) == {2: "DUPLICATE_TRIP"}


# 4. Re-run without duplicates.
def test_second_load_skips_and_adds_no_rows(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, JAN, synthetic.month_rows(JAN))
    first = _load(paths, con, JAN)
    before = _sizes(con)
    second = _load(paths, con, JAN)
    assert second.action == "skipped"
    assert second.line() == f"skip {JAN}: already loaded"
    assert _sizes(con) == before
    assert first.fact_rows == _count(con, "SELECT count(*) FROM fact_trip_hourly")


# 5. Rule order.
def test_a_row_failing_two_rules_gets_the_first_code(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    rows = [
        trip(JAN, 0, trip_miles=-1.0, tolls=-1.0),  # rules 5 and 4
        trip(JAN, 1, PULocationID=None, trip_time=0),  # rules 6 and 1
        trip(JAN, 2, DOLocationID=999, base_passenger_fare=-3.0),  # rules 4 and 3
    ]
    place_month(paths, con, JAN, rows)
    _load(paths, con, JAN)
    assert _codes(con) == {0: "NEGATIVE_FARE", 1: "NULL_REQUIRED", 2: "UNKNOWN_ZONE"}


def test_rule_list_is_ordered_and_matches_the_ddl() -> None:
    assert [r.order for r in rules.RULES] == list(range(1, len(rules.RULES) + 1))
    ddl = (config.SQL_DIR / "schema" / "06_quarantine_trip.sql").read_text(encoding="utf-8")
    assert all(f"'{code}'" in ddl for code in rules.CODES)
    with pytest.raises(ValueError):
        rules.case_sql(JAN, [])
    assert rules.next_month("2024-12") == "2025-01"


# 6. The fee column.
def test_fee_missing_before_2025_loads_with_zero_fee(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, DEC, [trip(DEC, 0), trip(DEC, 1)], fee=False)
    result = _load(paths, con, DEC)
    assert result.rows_kept == 2
    assert result.rows_fee_part_null == 0  # a column missing from the file is not a null part
    assert _count(con, "SELECT sum(cbd_fee_sum) FROM fact_trip_hourly") == 0
    assert _count(con, "SELECT sum(cbd_fee_trip_count) FROM fact_trip_hourly") == 0


def test_fee_missing_in_2025_is_a_schema_error(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, JAN, [trip(JAN, 0)], fee=False)
    with pytest.raises(schema.SchemaError, match="SCHEMA_MISSING_COLUMN"):
        _load(paths, con, JAN)


# 7. Both source type sets.
def test_both_source_type_sets_load_to_the_same_values(tmp_path: Path) -> None:
    sums = []
    for text, zone in ((pa.string(), pa.int64()), (pa.large_string(), pa.int32())):
        p = config.Paths(tmp_path / str(text))
        synthetic.write_zone_lookup(p.raw / config.ZONE_LOOKUP_FILE)
        c = db.connect(p.db, p.tmp, memory_limit_mb=500)
        try:
            dims.init_warehouse(c, p.raw / config.ZONE_LOOKUP_FILE, CBD)
            place_month(p, c, DEC, synthetic.month_rows(DEC), text=text, zone=zone, fee=False)
            load.load_month(DEC, p, c, free_fn=_plenty)
            sums.append(
                c.execute(
                    "SELECT count(*), sum(trip_count), sum(pu_zone_key::BIGINT * do_zone_key), "
                    "round(sum(rider_price_sum), 6) FROM fact_trip_hourly"
                ).fetchone()
            )
        finally:
            c.close()
    assert sums[0] == sums[1]


# 8. Other platforms.
def test_other_platforms_are_counted_not_kept_or_quarantined(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    rows = [trip(JAN, 0), trip(JAN, 1, hvfhs_license_num="HV0005")]
    rows += [trip(JAN, 2 + k, hvfhs_license_num=synthetic.OTHER_LICENSE) for k in range(3)]
    rows.append(trip(JAN, 9, hvfhs_license_num=None))
    place_month(paths, con, JAN, rows)
    result = _load(paths, con, JAN)
    assert (result.rows_kept, result.rows_other_platform, result.rows_quarantined) == (2, 3, 1)
    assert _codes(con) == {5: "NULL_REQUIRED"}
    platforms = con.execute(
        "SELECT platform_key, sum(trip_count) FROM fact_trip_hourly GROUP BY 1 ORDER BY 1"
    ).fetchall()
    assert platforms == [(1, 1), (2, 1)]


# 9. Null fee parts.
def test_null_fee_parts_count_as_zero(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    full = trip(JAN, 0)
    parts = ("tolls", "bcf", "sales_tax", "congestion_surcharge", "airport_fee")
    price = full["base_passenger_fare"] + sum(full[p] for p in parts) + full["cbd_congestion_fee"]
    rows = [full, trip(JAN, 1, airport_fee=None, cbd_congestion_fee=None)]
    place_month(paths, con, JAN, rows)
    result = _load(paths, con, JAN)
    assert result.rows_kept == 2
    assert result.rows_fee_part_null == 1
    got = con.execute(
        "SELECT sum(rider_price_sum), sum(cbd_fee_sum), sum(cbd_fee_trip_count) "
        "FROM fact_trip_hourly"
    ).fetchone()
    assert got is not None
    second = price - full["airport_fee"] - full["cbd_congestion_fee"]
    assert got[0] == pytest.approx(price + second)
    assert got[1] == pytest.approx(full["cbd_congestion_fee"])
    assert got[2] == 1
    assert _log(con, JAN)[4] == 1


# 10. Backfill.
def test_backfill_with_the_same_file_changes_no_counts(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, JAN, synthetic.month_rows(JAN))
    _load(paths, con, JAN)
    before, log_before = _sizes(con), _log(con, JAN)
    again = _load(paths, con, JAN, backfill=True)
    assert again.action == "reloaded"
    assert _sizes(con) == before
    assert _log(con, JAN) == log_before


def test_changed_file_needs_backfill_then_replaces_rows(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, JAN, [trip(JAN, i) for i in range(4)])
    _load(paths, con, JAN)
    place_month(paths, con, JAN, [trip(JAN, i) for i in range(6)] + [trip(JAN, 99, trip_time=0)])
    with pytest.raises(load.NeedsBackfillError):
        _load(paths, con, JAN)
    result = _load(paths, con, JAN, backfill=True)
    assert (result.action, result.rows_kept, result.rows_quarantined) == ("reloaded", 6, 1)
    assert _count(con, "SELECT sum(trip_count) FROM fact_trip_hourly") == 6
    statuses = con.execute("SELECT status FROM load_log ORDER BY load_id").fetchall()
    assert statuses == [("replaced",), ("loaded",)]
    assert _count(con, "SELECT count(DISTINCT load_id) FROM fact_trip_hourly") == 1


# 11. A failure inside the write step.
def test_failure_inside_the_write_leaves_no_partial_rows(
    paths: config.Paths, con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    place_month(paths, con, JAN, synthetic.month_rows(JAN))
    before = _sizes(con)

    def boom(*args: object) -> None:
        raise load.LoadError("patched failure")

    monkeypatch.setattr(load, "verify", boom)
    with pytest.raises(load.LoadError, match="patched"):
        _load(paths, con, JAN)
    assert _sizes(con) == before
    assert _count(con, "SELECT count(*) FROM load_log WHERE status = 'loaded'") == 0
    tables = {r[0] for r in con.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert "stg_trip" not in tables
    monkeypatch.undo()
    assert _load(paths, con, JAN).action == "loaded"


def test_verify_catches_counts_that_do_not_add_up(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, JAN, [trip(JAN, 0)])
    _load(paths, con, JAN)
    load_id = _count(con, "SELECT load_id FROM load_log")
    bad = load.LoadResult(JAN, "loaded", load_id, rows_in_file=5, rows_kept=1)
    with pytest.raises(load.LoadError, match="row_count"):
        load.verify(con, bad)
    bad = load.LoadResult(JAN, "loaded", load_id, rows_in_file=2, rows_kept=2)
    with pytest.raises(load.LoadError, match="fact trips 1 != rows_kept 2"):
        load.verify(con, bad)
    con.execute("UPDATE fact_trip_hourly SET do_zone_key = 999")
    ok_counts = load.LoadResult(JAN, "loaded", load_id, rows_in_file=1, rows_kept=1)
    with pytest.raises(load.LoadError, match="integrity"):
        load.verify(con, ok_counts)


# 14. The congestion-zone flag.
def test_in_congestion_zone_matches_the_plan(tmp_path: Path) -> None:
    treated = sorted(params.load_params()["treated_zones"])
    assert dims.cbd_zone_ids() == treated
    p = config.Paths(tmp_path)
    lookup = synthetic.write_zone_lookup(p.raw / config.ZONE_LOOKUP_FILE)
    c = db.connect(":memory:", p.tmp)
    try:
        dims.init_warehouse(c, lookup)
        flagged = [
            r[0]
            for r in c.execute(
                "SELECT zone_key FROM dim_zone WHERE in_congestion_zone ORDER BY 1"
            ).fetchall()
        ]
        assert flagged == treated
    finally:
        c.close()


def test_dimensions_are_filled_and_zone_changes_fail(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    assert _count(con, "SELECT count(*) FROM dim_zone") == synthetic.ZONE_COUNT
    assert _count(con, "SELECT count(*) FROM dim_zone WHERE service_zone IS NULL") == 1
    assert _count(con, "SELECT count(*) FROM dim_platform") == 2
    assert _count(con, "SELECT min(date_key) FROM dim_date") == 20230101
    assert _count(con, "SELECT max(date_key) FROM dim_date") == 20251231
    row = con.execute(
        "SELECT iso_day_of_week, is_weekend, month_key FROM dim_date WHERE date_key = 20250105"
    ).fetchone()
    assert row == (7, True, 202501)
    lookup = paths.raw / config.ZONE_LOOKUP_FILE
    dims.init_warehouse(con, lookup, CBD)  # twice is safe
    assert _count(con, "SELECT count(*) FROM dim_zone") == synthetic.ZONE_COUNT
    with pytest.raises(dims.ZoneChangeError, match="4"):
        dims.load_zones(con, lookup, [12, 13])
    synthetic.write_zone_lookup(lookup, zone_count=200)
    with pytest.raises(dims.ZoneChangeError):
        dims.load_zones(con, lookup, CBD)


# 15. The fact primary key.
def test_fact_primary_key_rejects_a_repeated_cell(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    place_month(paths, con, JAN, [trip(JAN, 0), trip(JAN, 1)])
    _load(paths, con, JAN)
    with pytest.raises(duckdb.ConstraintException):
        con.execute("INSERT INTO fact_trip_hourly SELECT * FROM fact_trip_hourly LIMIT 1")


def test_load_refuses_unlogged_files_and_short_disk(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    with pytest.raises(load.LoadError, match="missing"):
        _load(paths, con, JAN)
    synthetic.write_month(paths.raw / "fhvhv_tripdata_2025-01.parquet", [trip(JAN, 0)])
    with pytest.raises(load.LoadError, match="no current load_log row"):
        _load(paths, con, JAN)
    place_month(paths, con, JAN, [trip(JAN, 0)])
    with pytest.raises(load.LoadDiskError):
        load.load_month(JAN, paths, con, free_fn=lambda _: 0)
    empty = db.connect(":memory:", paths.tmp)
    try:
        db.run_schema(empty)
        with pytest.raises(load.LoadError, match="init"):
            load.load_month(JAN, paths, empty, free_fn=_plenty)
    finally:
        empty.close()


def test_cli_init_load_all_and_summary(
    paths: config.Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    root = ["--root", str(paths.root)]
    assert cli.main([*root, "load", "--all"], free_fn=_plenty) == config.EXIT_FAILED
    assert cli.main([*root, "init"]) == config.EXIT_OK
    c = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    try:
        place_month(paths, c, DEC, synthetic.month_rows(DEC), fee=False)
        place_month(paths, c, JAN, synthetic.month_rows(JAN))
    finally:
        c.close()
    assert cli.main([*root, "load", "--all"], free_fn=_plenty) == config.EXIT_OK
    assert cli.main([*root, "load", "--all"], free_fn=_plenty) == config.EXIT_OK
    out = capsys.readouterr().out
    assert f"loaded {DEC}: kept" in out and f"skip {JAN}: already loaded" in out
    assert cli.main([*root, "summary"]) == config.EXIT_OK
    report = json.loads((paths.reports / "load_summary.json").read_text(encoding="utf-8"))
    assert report["months_loaded"] == 2
    for m in report["months"]:
        assert m["quarantined_by_reason"]["DUPLICATE_TRIP"] == 2
        assert all(m["quarantined_by_reason"][c] == 1 for c in rules.CODES[:-1])
        assert (
            m["rows_kept"] + m["rows_quarantined"] + m["rows_other_platform"] == m["rows_in_file"]
        )
        assert m["warehouse_bytes_after_load"] > 0
    assert report["totals"]["rows_quarantined"] == 2 * len(rules.CODES[:-1]) + 4
    empty = _empty(paths)
    try:
        assert summary.load_summary(empty)["months_loaded"] == 0
    finally:
        empty.close()


def _empty(paths: config.Paths) -> duckdb.DuckDBPyConnection:
    c = db.connect(":memory:", paths.tmp)
    db.run_schema(c)
    return c


def test_cli_exit_codes(paths: config.Paths, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(paths.root)]
    assert cli.main([*root, "init"]) == config.EXIT_OK
    c = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    try:
        place_month(paths, c, JAN, [trip(JAN, 0)])
    finally:
        c.close()
    args = [*root, "load", "--month", JAN]
    assert cli.main(args, free_fn=lambda _: 0) == config.EXIT_DISK
    assert cli.main(args, free_fn=_plenty) == config.EXIT_OK
    c = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    try:
        place_month(paths, c, JAN, [trip(JAN, 5)])
    finally:
        c.close()
    assert cli.main(args, free_fn=_plenty) == config.EXIT_NEEDS_BACKFILL
    assert cli.main([*root, "load", "--month", DEC], free_fn=_plenty) == config.EXIT_FAILED
    (paths.raw / config.ZONE_LOOKUP_FILE).unlink()
    assert cli.main([*root, "init"]) == config.EXIT_FAILED
    synthetic.write_zone_lookup(paths.raw / config.ZONE_LOOKUP_FILE, zone_count=100)
    assert cli.main([*root, "init"]) == config.EXIT_FAILED
    assert "FAIL" in capsys.readouterr().out
