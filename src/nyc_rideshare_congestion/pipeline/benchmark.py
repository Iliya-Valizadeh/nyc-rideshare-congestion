"""`nrc benchmark`: the three transforms on a three-month sample, in DuckDB and PySpark.

This is scale evidence, not a claim of scale. The steps:

1. Export three months of `fact_trip_hourly` and the three dimensions to Parquet, one
   month at a time, from the warehouse.
2. Run the DuckDB transforms in `sql/transforms/` on that Parquet, in a child process.
3. Run the Spark SQL copies in `sql/spark/` on the same Parquet with PySpark in local
   mode, in another child process.
4. Compare the two sets of result tables row by row on their keys.

Each engine gets two threads and a 2 GB memory setting. Each child process is watched
from outside: its peak memory is the largest sum of resident memory over the process
and its children (the Spark JVM is a child), sampled every 50 ms. The time per
transform is measured inside the child, from the start of the query until the result
is in Python as an Arrow table. PySpark and psutil are in the optional `bench`
dependency group (`uv sync --group bench`), so CI does not install them. Spark needs a
Java runtime; JAVA_HOME must point to one.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from nyc_rideshare_congestion import config

DUCKDB_SQL = config.SQL_DIR / "transforms"
SPARK_SQL = config.SQL_DIR / "spark"
SAMPLE_MONTHS = ("2025-01", "2025-02", "2025-03")
THREADS = 2
MEMORY = "2GB"
SPARK_MEMORY = "2g"
POLL_S = 0.05
REPORT = "benchmark.json"

# Result table: (transform file name, key columns).
TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "tf_hourly_demand": ("01_hourly_demand.sql", ("date_key", "pickup_hour", "borough")),
    "tf_platform_share": ("02_platform_share.sql", ("month_key", "borough", "platform_key")),
    "tf_fare_per_mile_by_zone": (
        "03_fare_per_mile_by_zone.sql",
        ("month_key", "pu_zone_key", "platform_key"),
    ),
}
# Input tables and the columns the transforms read.
INPUTS: dict[str, tuple[str, ...]] = {
    "fact_trip_hourly": (
        "date_key",
        "pickup_hour",
        "platform_key",
        "pu_zone_key",
        "trip_count",
        "trip_miles_sum",
        "base_fare_sum",
    ),
    "dim_date": ("date_key", "full_date", "year", "month", "month_key"),
    "dim_zone": ("zone_key", "borough", "zone_name"),
    "dim_platform": ("platform_key", "platform_name"),
}


def _q(path: Path) -> str:
    """A path as a quoted SQL string literal."""
    return "'" + str(path).replace("\\", "/").replace("'", "''") + "'"


def export_sample(
    con: duckdb.DuckDBPyConnection, months: tuple[str, ...], out: Path
) -> dict[str, int]:
    """Write the sample's input tables to out/input/. The fact table goes month by month."""
    rows: dict[str, int] = {}
    fact_dir = out / "input" / "fact_trip_hourly"
    fact_dir.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(INPUTS["fact_trip_hourly"])
    total = 0
    for month in months:
        key = int(month.replace("-", ""))
        path = fact_dir / f"{month}.parquet"
        con.execute(
            f"COPY (SELECT {cols} FROM fact_trip_hourly WHERE date_key // 100 = {key}) "
            f"TO {_q(path)} (FORMAT parquet)"
        )
        got = con.execute(f"SELECT count(*) FROM read_parquet({_q(path)})").fetchone()
        n = int(got[0]) if got else 0
        if n == 0:
            raise ValueError(f"month {month} has no fact rows; load it first")
        total += n
    rows["fact_trip_hourly"] = total
    first = int(months[0].replace("-", "")) * 100
    last = int(months[-1].replace("-", "")) * 100 + 99
    for name, columns in INPUTS.items():
        if name == "fact_trip_hourly":
            continue
        where = f" WHERE date_key BETWEEN {first} AND {last}" if name == "dim_date" else ""
        path = out / "input" / f"{name}.parquet"
        con.execute(
            f"COPY (SELECT {', '.join(columns)} FROM {name}{where}) TO {_q(path)} (FORMAT parquet)"
        )
        got = con.execute(f"SELECT count(*) FROM read_parquet({_q(path)})").fetchone()
        rows[name] = int(got[0]) if got else 0
    return rows


def _input_path(sample: Path, name: str) -> Path:
    if name == "fact_trip_hourly":
        return sample / "input" / name / "*.parquet"
    return sample / "input" / f"{name}.parquet"


def run_duckdb(sample: Path, out: Path, sql_dir: Path = DUCKDB_SQL) -> dict[str, float]:
    """Run the DuckDB transforms on the sample. Returns seconds per table."""
    out.mkdir(parents=True, exist_ok=True)
    tmp = sample / "duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(
        ":memory:",
        config={"memory_limit": MEMORY, "threads": THREADS, "temp_directory": str(tmp)},
    )
    try:
        for name in INPUTS:
            con.execute(
                f"CREATE VIEW {name} AS SELECT * FROM read_parquet({_q(_input_path(sample, name))})"
            )
        seconds: dict[str, float] = {}
        for table, (file, _) in TABLES.items():
            text = (sql_dir / file).read_text(encoding="utf-8")
            start = time.perf_counter()
            con.execute(text)
            result = con.table(table).arrow()
            seconds[table] = time.perf_counter() - start
            pq.write_table(_as_table(result), out / f"{table}.parquet")
        return seconds
    finally:
        con.close()


def _as_table(result: Any) -> pa.Table:
    """DuckDB may hand back a record batch reader; make it a table."""
    if isinstance(result, pa.Table):
        return result
    table: pa.Table = result.read_all()
    return table


def run_spark(  # pragma: no cover
    sample: Path, out: Path, sql_dir: Path = SPARK_SQL
) -> dict[str, float]:
    """Run the Spark SQL copies with PySpark in local mode. Returns seconds per table.

    Not run in CI: it needs the optional `bench` group and a Java runtime.
    """
    from pyspark.sql import SparkSession

    out.mkdir(parents=True, exist_ok=True)
    spark = (
        SparkSession.builder.master(f"local[{THREADS}]")
        .appName("nrc-benchmark")
        .config("spark.driver.memory", SPARK_MEMORY)
        .config("spark.sql.shuffle.partitions", str(THREADS * 4))
        .config("spark.ui.enabled", "false")
        .config("spark.sql.session.timeZone", "UTC")
        .getOrCreate()
    )
    try:
        for name in INPUTS:
            spark.read.parquet(str(_input_path(sample, name))).createOrReplaceTempView(name)
        seconds: dict[str, float] = {}
        for table, (file, _) in TABLES.items():
            text = (sql_dir / file).read_text(encoding="utf-8")
            start = time.perf_counter()
            result = spark.sql(text).toArrow()
            seconds[table] = time.perf_counter() - start
            pq.write_table(result, out / f"{table}.parquet")
        return seconds
    finally:
        spark.stop()


def compare(a_dir: Path, b_dir: Path) -> dict[str, Any]:
    """Join each result table from the two engines on its keys and count differences.

    "Exact" means equal as stored, with nulls equal to nulls. For number columns the
    largest absolute difference is also given, so a difference in the last digit of a
    float sum can be told apart from a wrong value.
    """
    con = duckdb.connect(":memory:")
    out: dict[str, Any] = {}
    try:
        for table, (_, keys) in TABLES.items():
            a, b = _q(a_dir / f"{table}.parquet"), _q(b_dir / f"{table}.parquet")
            con.execute(f"CREATE OR REPLACE VIEW a AS SELECT * FROM read_parquet({a})")
            con.execute(f"CREATE OR REPLACE VIEW b AS SELECT * FROM read_parquet({b})")
            described = con.execute("DESCRIBE a").fetchall()
            types = {str(r[0]): str(r[1]) for r in described}
            on = " AND ".join(f"a.{k} = b.{k}" for k in keys)
            row = con.execute(
                f"""
                SELECT
                    (SELECT count(*) FROM a), (SELECT count(*) FROM b),
                    count(*) FILTER (WHERE b.{keys[0]} IS NULL),
                    count(*) FILTER (WHERE a.{keys[0]} IS NULL)
                FROM a FULL OUTER JOIN b ON {on}
                """
            ).fetchone()
            assert row is not None
            columns: dict[str, Any] = {}
            for col, kind in types.items():
                if col in keys:
                    continue
                numeric = not kind.startswith(("VARCHAR", "DATE", "BOOLEAN"))
                diff = (
                    f"max(abs(CAST(a.{col} AS DOUBLE) - CAST(b.{col} AS DOUBLE)))"
                    if numeric
                    else "NULL"
                )
                got = con.execute(
                    f"SELECT count(*) FILTER (WHERE a.{col} IS DISTINCT FROM b.{col}), {diff} "
                    f"FROM a JOIN b ON {on}"
                ).fetchone()
                assert got is not None
                columns[col] = {
                    "rows_not_exact": int(got[0]),
                    "max_abs_difference": None if got[1] is None else float(got[1]),
                }
            out[table] = {
                "rows_duckdb": int(row[0]),
                "rows_spark": int(row[1]),
                "rows_only_in_duckdb": int(row[2]),
                "rows_only_in_spark": int(row[3]),
                "columns": columns,
                "all_exact": int(row[2]) == 0
                and int(row[3]) == 0
                and int(row[0]) == int(row[1])
                and all(c["rows_not_exact"] == 0 for c in columns.values()),
            }
        return out
    finally:
        con.close()


def measure(cmd: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    """Run a child process and record its wall time and peak memory (with children).

    Returns the JSON object the child prints on its last line of output.
    """
    import psutil

    start = time.perf_counter()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    watched = psutil.Process(proc.pid)
    peak = 0
    while proc.poll() is None:
        try:
            tree = [watched, *watched.children(recursive=True)]
            peak = max(peak, sum(p.memory_info().rss for p in tree if p.is_running()))
        except psutil.Error:
            pass
        time.sleep(POLL_S)
    stdout, stderr = proc.communicate()
    wall = time.perf_counter() - start
    if proc.returncode != 0:
        tail = stderr.decode(errors="replace")[-2000:]
        raise RuntimeError(f"{cmd[-3]} failed with exit code {proc.returncode}: {tail}")
    last = stdout.decode(errors="replace").strip().splitlines()[-1]
    child: dict[str, Any] = json.loads(last)
    return {
        "wall_seconds_with_startup": wall,
        "peak_memory_mb": peak / 2**20,
        "seconds_by_table": child["seconds"],
        "seconds_all_tables": sum(child["seconds"].values()),
        "version": child["version"],
    }


def _java_version(env: dict[str, str]) -> str:
    home = env.get("JAVA_HOME")
    if not home:
        return "JAVA_HOME not set"
    java = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
    done = subprocess.run([str(java), "-version"], capture_output=True, text=True, check=False)
    return (done.stderr or done.stdout).strip().splitlines()[0]


def run_benchmark(
    con: duckdb.DuckDBPyConnection, paths: config.Paths, months: tuple[str, ...] = SAMPLE_MONTHS
) -> dict[str, Any]:  # pragma: no cover
    """The whole benchmark. Writes reports/benchmark.json. Needs the `bench` group."""
    import psutil

    sample = paths.tmp / "benchmark"
    rows = export_sample(con, months, sample)
    con.close()  # free the warehouse cache before the engines run
    env = dict(os.environ)
    me = [sys.executable, "-m", "nyc_rideshare_congestion.pipeline.benchmark"]
    results = {
        "duckdb": measure([*me, "duckdb", str(sample), str(sample / "out_duckdb")], env),
        "spark": measure([*me, "spark", str(sample), str(sample / "out_spark")], env),
    }
    report: dict[str, Any] = {
        "what": "the three transforms on a three-month sample, on one laptop",
        "months": list(months),
        "input_rows": rows,
        "settings": {
            "threads": THREADS,
            "duckdb_memory_limit": MEMORY,
            "spark_driver_memory": SPARK_MEMORY,
            "spark_master": f"local[{THREADS}]",
            "memory_sampled_every_s": POLL_S,
        },
        "machine": {
            "os": platform.platform(),
            "cpu_count": os.cpu_count(),
            "ram_gb": round(psutil.virtual_memory().total / 2**30, 1),
            "python": platform.python_version(),
            "java": _java_version(env),
        },
        "engines": results,
        "comparison": compare(sample / "out_duckdb", sample / "out_spark"),
    }
    report["all_tables_exact"] = all(t["all_exact"] for t in report["comparison"].values())
    path = paths.reports / REPORT
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def _child(argv: list[str]) -> int:  # pragma: no cover
    """Entry point for one engine run in a child process. Prints one JSON line."""
    engine, sample, out = argv[0], Path(argv[1]), Path(argv[2])
    if engine == "duckdb":
        seconds, version = run_duckdb(sample, out), duckdb.__version__
    else:
        import pyspark

        seconds, version = run_spark(sample, out), pyspark.__version__
    print(json.dumps({"seconds": seconds, "version": version}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_child(sys.argv[1:]))
