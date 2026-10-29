"""Time one query before and after the drop-off zone index (ADR 0003 and 0007).

The query is the one ADR 0003 names: daily trips for one drop-off zone. It runs for
three zones picked by a fixed rule (the zones with the most, the median and the fewest
fact rows), because DuckDB only uses an index when a filter matches few rows. Each
query runs once untimed, then `TIMING_RUNS` times. The plan is read with EXPLAIN
ANALYZE to record whether DuckDB used the index (see `uses_index`).

Two parts, both in one report:

1. The whole fact table: time the query, then try to build the index. DuckDB builds an
   index in memory, so under the memory cap this can run out of memory. That outcome
   is recorded, not hidden.
2. A slice: the last `TIMING_SLICE_MONTHS` loaded months, copied into a scratch
   database in `data/tmp/`. The query is timed on the copy as loaded, on a second copy
   sorted by drop-off zone (DuckDB's min-max zone maps then skip most blocks), and on
   the first copy after the index is built. The scratch file is removed at the end.

Timings come from one machine on one day. They are not a benchmark.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from nyc_rideshare_congestion import config

INDEX_NAME = "ix_fact_do_zone"
INDEX_SQL = config.SQL_DIR / "index" / f"{INDEX_NAME}.sql"
QUERY = (
    "SELECT date_key, sum(trip_count) AS trips FROM {table} "
    "WHERE do_zone_key = {zone} GROUP BY date_key ORDER BY date_key"
)
SCRATCH_ALIAS = "timing"
SORTED_TABLE = "fact_by_do_zone"


def total_ram_bytes() -> int | None:
    """Physical memory of this machine, or None if the platform does not say."""
    if sys.platform == "win32":

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
        return None  # pragma: no cover
    try:  # pragma: no cover - not run on the Windows machine used here
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (ValueError, OSError, AttributeError):  # pragma: no cover
        return None


def _setting(con: duckdb.DuckDBPyConnection, name: str) -> str:
    row = con.execute("SELECT current_setting(?)", [name]).fetchone()
    return str(row[0]) if row else ""


def machine(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    ram = total_ram_bytes()
    return {
        "os": platform.platform(),
        "cpu": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        "ram_gb": round(ram / 1024**3, 1) if ram else None,
        "python": platform.python_version(),
        "duckdb": duckdb.__version__,
        "duckdb_memory_limit": _setting(con, "memory_limit"),
        "duckdb_threads": _setting(con, "threads"),
        "index_scan_percentage": _setting(con, "index_scan_percentage"),
        "index_scan_max_count": _setting(con, "index_scan_max_count"),
    }


def pick_zones(
    con: duckdb.DuckDBPyConnection, table: str = "fact_trip_hourly"
) -> dict[str, tuple[int, int]]:
    """The drop-off zones with the most, the median and the fewest fact rows.
    Ties go to the lower zone key. Returns label -> (zone key, fact rows)."""
    rows = con.execute(
        f"SELECT do_zone_key, count(*) AS n FROM {table} GROUP BY 1 ORDER BY n DESC, 1"
    ).fetchall()
    if not rows:
        raise ValueError(f"{table} is empty; nothing to time")
    picked = {"most_rows": rows[0], "median_rows": rows[len(rows) // 2], "fewest_rows": rows[-1]}
    return {k: (int(z), int(n)) for k, (z, n) in picked.items()}


def uses_index(con: duckdb.DuckDBPyConnection, sql: str) -> bool:
    """Whether the query ran with an index scan. DuckDB picks the scan type when the
    query runs, so plain EXPLAIN always shows a sequential scan. This reads the plan
    from EXPLAIN ANALYZE, which runs the query once more, untimed."""
    plan = "\n".join(str(r[-1]) for r in con.execute("EXPLAIN ANALYZE " + sql).fetchall())
    return "INDEX_SCAN" in plan.upper().replace(" ", "_")


def time_query(con: duckdb.DuckDBPyConnection, sql: str, runs: int) -> dict[str, Any]:
    con.execute(sql).fetchall()  # warm-up, not timed
    seconds = []
    for _ in range(runs):
        start = time.perf_counter()
        con.execute(sql).fetchall()
        seconds.append(time.perf_counter() - start)
    return {
        "median_ms": round(statistics.median(seconds) * 1000, 1),
        "min_ms": round(min(seconds) * 1000, 1),
        "runs": runs,
        "uses_index": uses_index(con, sql),
    }


def time_zones(
    con: duckdb.DuckDBPyConnection, table: str, zones: dict[str, tuple[int, int]], runs: int
) -> dict[str, dict[str, Any]]:
    return {
        label: time_query(con, QUERY.format(table=table, zone=int(zone)), runs)
        for label, (zone, _) in zones.items()
    }


def has_index(con: duckdb.DuckDBPyConnection) -> bool:
    """True if the current database has the timed index."""
    row = con.execute(
        "SELECT count(*) FROM duckdb_indexes() "
        "WHERE index_name = ? AND database_name = current_database()",
        [INDEX_NAME],
    ).fetchone()
    return bool(row and row[0])


def build_index(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Run sql/index/ix_fact_do_zone.sql on the current database. Running out of memory
    is a result to record, so it is caught. Any other error is raised."""
    start = time.perf_counter()
    try:
        con.execute(INDEX_SQL.read_text(encoding="utf-8"))
    except duckdb.OutOfMemoryException as err:
        return {
            "status": "out_of_memory",
            "seconds": round(time.perf_counter() - start, 2),
            "error": str(err).splitlines()[0],
        }
    return {"status": "built", "seconds": round(time.perf_counter() - start, 2), "error": None}


def _speedup(before: dict[str, Any], after: dict[str, Any] | None) -> float | None:
    if after is None or not after["median_ms"]:
        return None
    return round(float(before["median_ms"]) / float(after["median_ms"]), 2)


def _count(con: duckdb.DuckDBPyConnection, table: str) -> int:
    row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def time_full_table(con: duckdb.DuckDBPyConnection, runs: int) -> dict[str, Any]:
    """Drop the index if present, time, try to build it, and time again if it built."""
    con.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    zones = pick_zones(con)
    before = time_zones(con, "fact_trip_hourly", zones, runs)
    build = build_index(con)
    after = time_zones(con, "fact_trip_hourly", zones, runs) if build["status"] == "built" else {}
    return {
        "fact_rows": _count(con, "fact_trip_hourly"),
        "index_build": build,
        "zones": [
            {
                "label": label,
                "do_zone_key": zone,
                "fact_rows_for_zone": n,
                "before": before[label],
                "after": after.get(label),
                "speedup": _speedup(before[label], after.get(label)),
            }
            for label, (zone, n) in zones.items()
        ],
    }


def _slice_months(con: duckdb.DuckDBPyConnection, n_months: int) -> list[int]:
    rows = con.execute(
        "SELECT DISTINCT date_key // 100 AS m FROM fact_trip_hourly ORDER BY m DESC LIMIT ?",
        [n_months],
    ).fetchall()
    return sorted(int(m) for (m,) in rows)


def time_slice(
    con: duckdb.DuckDBPyConnection, scratch: Path, runs: int, n_months: int
) -> dict[str, Any]:
    """Copy the last n_months of the fact into a scratch database and time the query
    there: as loaded, sorted by drop-off zone, and with the index."""
    months = _slice_months(con, n_months)
    home = con.execute("SELECT current_database()").fetchone()
    home_name = str(home[0]) if home else "memory"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.unlink(missing_ok=True)
    con.execute(f"ATTACH '{scratch.as_posix()}' AS {SCRATCH_ALIAS}")
    try:
        start = time.perf_counter()
        con.execute(
            f"CREATE TABLE {SCRATCH_ALIAS}.fact_trip_hourly AS "
            "SELECT * FROM fact_trip_hourly WHERE date_key >= ?",
            [months[0] * 100],
        )
        copy_s = time.perf_counter() - start
        start = time.perf_counter()
        con.execute(
            f"CREATE TABLE {SCRATCH_ALIAS}.{SORTED_TABLE} AS "
            f"SELECT * FROM {SCRATCH_ALIAS}.fact_trip_hourly ORDER BY do_zone_key"
        )
        sort_s = time.perf_counter() - start
        con.execute(f"USE {SCRATCH_ALIAS}")
        zones = pick_zones(con)
        before = time_zones(con, "fact_trip_hourly", zones, runs)
        by_zone = time_zones(con, SORTED_TABLE, zones, runs)
        build = build_index(con)
        built = build["status"] == "built"
        after = time_zones(con, "fact_trip_hourly", zones, runs) if built else {}
        rows = _count(con, "fact_trip_hourly")
    finally:
        con.execute(f'USE "{home_name}"')
        con.execute(f"DETACH {SCRATCH_ALIAS}")
        scratch.unlink(missing_ok=True)
    return {
        "months": [f"{str(m)[:4]}-{str(m)[4:]}" for m in months],
        "fact_rows": rows,
        "copy_s": round(copy_s, 2),
        "sorted_copy_s": round(sort_s, 2),
        "index_build": build,
        "zones": [
            {
                "label": label,
                "do_zone_key": zone,
                "fact_rows_for_zone": n,
                "before": before[label],
                "sorted_by_do_zone": by_zone[label],
                "after_index": after.get(label),
                "speedup_sorted": _speedup(before[label], by_zone[label]),
                "speedup_index": _speedup(before[label], after.get(label)),
            }
            for label, (zone, n) in zones.items()
        ],
    }


def run_timing(
    con: duckdb.DuckDBPyConnection,
    scratch: Path,
    runs: int = config.TIMING_RUNS,
    slice_months: int = config.TIMING_SLICE_MONTHS,
) -> dict[str, Any]:
    """Both parts of the timing. If the full-table index builds, it stays."""
    full = time_full_table(con, runs)
    part = time_slice(con, scratch, runs, slice_months)
    return {
        "command": "uv run nrc time-index",
        "measured_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": (
            "Single-machine numbers: one run of this script on the machine below, with "
            "the DuckDB limits shown. Not a benchmark; other machines will differ."
        ),
        "machine": machine(con),
        "index": INDEX_SQL.read_text(encoding="utf-8").strip().splitlines()[-1],
        "query": QUERY,
        "full_table": full,
        "slice": part,
    }


def write_report(result: dict[str, Any], reports: Path) -> Path:
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / config.INDEX_TIMING_REPORT
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path
