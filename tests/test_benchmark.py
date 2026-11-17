"""The benchmark's DuckDB half and its comparison, on a tiny warehouse.

PySpark and psutil are in the optional `bench` group, so CI runs only the parts that
need neither. The Spark run itself is checked by hand with `nrc benchmark`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

from nyc_rideshare_congestion.pipeline import benchmark, db, dims

MONTHS = ("2025-01", "2025-02", "2025-03")


@pytest.fixture
def con(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    c = db.connect(tmp_path / "w.duckdb", tmp_path / "tmp", memory_limit_mb=500)
    db.run_schema(c)
    c.executemany(
        "INSERT INTO dim_zone VALUES (?, ?, ?, 'Boro Zone', false)",
        [[4, "Manhattan", "zone 4"], [7, "Queens", "zone 7"], [11, "Brooklyn", "zone 11"]],
    )
    dims.fill_dates(c, "2024-12-01", "2025-04-30")
    c.execute(
        """
        INSERT INTO fact_trip_hourly
        SELECT d.date_key, h, p, z, 4, 1 + (d.date_key + h + z) % 5,
               2.5 * (1 + (d.date_key + h) % 3), 600, 18.0 + z, 20.0 + z, 0, 0, 1
        FROM dim_date AS d, range(0, 24, 6) AS t(h), (VALUES (1), (2)) AS pl(p),
             (VALUES (4), (7), (11)) AS zz(z)
        WHERE d.date_key BETWEEN 20241201 AND 20250430
        """
    )
    return c


def test_export_writes_only_the_sample_months(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    rows = benchmark.export_sample(con, MONTHS, tmp_path / "s")
    days = 31 + 28 + 31
    assert rows["fact_trip_hourly"] == days * 4 * 2 * 3
    assert rows["dim_date"] == days
    assert rows["dim_zone"] == 3 and rows["dim_platform"] == 2
    files = sorted(p.name for p in (tmp_path / "s" / "input" / "fact_trip_hourly").iterdir())
    assert files == [f"{m}.parquet" for m in MONTHS]


def test_export_refuses_a_month_with_no_rows(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="2025-06"):
        benchmark.export_sample(con, ("2025-06",), tmp_path / "s")


def test_duckdb_on_parquet_matches_the_warehouse_transforms(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    benchmark.export_sample(con, MONTHS, tmp_path / "s")
    seconds = benchmark.run_duckdb(tmp_path / "s", tmp_path / "a")
    assert set(seconds) == set(benchmark.TABLES)
    assert all(v >= 0 for v in seconds.values())
    # The same SQL on the warehouse, limited to the same months, gives the same rows.
    con.execute("DELETE FROM fact_trip_hourly WHERE date_key < 20250101 OR date_key > 20250331")
    for table, (file, keys) in benchmark.TABLES.items():
        con.execute((benchmark.DUCKDB_SQL / file).read_text(encoding="utf-8"))
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert n is not None
        assert pq.read_table(tmp_path / "a" / f"{table}.parquet").num_rows == n[0]
        assert keys[0] in pq.read_schema(tmp_path / "a" / f"{table}.parquet").names


def test_compare_finds_identical_tables_exact(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    benchmark.export_sample(con, MONTHS, tmp_path / "s")
    benchmark.run_duckdb(tmp_path / "s", tmp_path / "a")
    shutil.copytree(tmp_path / "a", tmp_path / "b")
    out = benchmark.compare(tmp_path / "a", tmp_path / "b")
    assert all(t["all_exact"] for t in out.values())


def test_compare_reports_a_changed_value_and_a_missing_row(
    con: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    benchmark.export_sample(con, MONTHS, tmp_path / "s")
    benchmark.run_duckdb(tmp_path / "s", tmp_path / "a")
    (tmp_path / "b").mkdir()
    edit = duckdb.connect(":memory:")
    for table in benchmark.TABLES:
        src = benchmark._q(tmp_path / "a" / f"{table}.parquet")
        dst = benchmark._q(tmp_path / "b" / f"{table}.parquet")
        query = f"SELECT * FROM read_parquet({src})"
        if table == "tf_platform_share":
            query = f"SELECT * REPLACE (trip_share + 1e-12 AS trip_share) FROM read_parquet({src})"
        if table == "tf_fare_per_mile_by_zone":
            query += " WHERE pu_zone_key <> 7"
        edit.execute(f"COPY ({query}) TO {dst} (FORMAT parquet)")
    edit.close()
    out = benchmark.compare(tmp_path / "a", tmp_path / "b")
    assert out["tf_hourly_demand"]["all_exact"]
    share = out["tf_platform_share"]
    assert not share["all_exact"]
    assert share["columns"]["trip_share"]["rows_not_exact"] == share["rows_duckdb"]
    assert share["columns"]["trip_share"]["max_abs_difference"] == pytest.approx(1e-12, rel=0.1)
    assert share["columns"]["platform_name"]["max_abs_difference"] is None
    fare = out["tf_fare_per_mile_by_zone"]
    assert fare["rows_only_in_duckdb"] > 0 and fare["rows_only_in_spark"] == 0
    assert not fare["all_exact"]


def test_spark_sql_copies_exist_for_every_transform() -> None:
    for file, _ in benchmark.TABLES.values():
        text = (benchmark.SPARK_SQL / file).read_text(encoding="utf-8")
        assert "CREATE" not in text.upper().split("--")[0] or text.startswith("--")
        assert "SELECT" in text
