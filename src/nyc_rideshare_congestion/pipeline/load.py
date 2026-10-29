"""Validate, quarantine and load one month into the star schema (ADR 0004, steps 1 to 5).

Steps 1 to 4 write only the staging table `stg_trip`. Step 5 is one transaction: it
writes the quarantine rows, the fact rows and the load_log counts, checks that the
counts add up and that every fact key is in its dimension table, then commits. Any
failure rolls the whole month back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from nyc_rideshare_congestion import __version__, config
from nyc_rideshare_congestion.pipeline import rules, schema
from nyc_rideshare_congestion.sources.tlc import DiskFreeFn, disk_free, sha256_file, trip_file_name

LOAD_SQL = config.SQL_DIR / "load"


class LoadError(RuntimeError):
    """The month could not load. Exit code 1."""


class NeedsBackfillError(LoadError):
    """The month is loaded from a different file. Exit code 3."""


class LoadDiskError(LoadError):
    """Not enough free disk for staging and spill files. Exit code 4."""


@dataclass
class LoadResult:
    month: str
    action: str  # "loaded", "reloaded" or "skipped"
    load_id: int
    rows_in_file: int = 0
    rows_other_platform: int = 0
    rows_kept: int = 0
    rows_quarantined: int = 0
    rows_fee_part_null: int = 0
    fact_rows: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def line(self) -> str:
        if self.action == "skipped":
            return f"skip {self.month}: already loaded"
        reasons = ", ".join(f"{k} {v}" for k, v in self.reasons.items() if v)
        return (
            f"{self.action} {self.month}: kept {self.rows_kept}, quarantined "
            f"{self.rows_quarantined} ({reasons or 'none'}), other platform "
            f"{self.rows_other_platform}, fact rows {self.fact_rows}"
        )


def _one(con: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None) -> int:
    row = con.execute(sql, params or []).fetchone()
    return 0 if row is None or row[0] is None else int(row[0])


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _licenses(con: duckdb.DuckDBPyConnection) -> str:
    """The in-scope license numbers from dim_platform, as a SQL list."""
    rows = con.execute("SELECT license_num FROM dim_platform ORDER BY 1").fetchall()
    return ", ".join("'" + str(r[0]).replace("'", "''") + "'" for r in rows)


def _sql(name: str) -> str:
    return (LOAD_SQL / name).read_text(encoding="utf-8")


def _month_bounds(month: str) -> tuple[int, int]:
    """First and last date_key a month can hold."""
    key = int(month.replace("-", ""))
    return key * 100 + 1, key * 100 + 31


def _log_state(
    con: duckdb.DuckDBPyConnection, month: str, name: str, sha256: str
) -> tuple[int, str, list[int]]:
    """Return this file's (load_id, status), and the month's other loaded load_ids."""
    row = con.execute(
        "SELECT load_id, status FROM load_log WHERE file_name = ? AND sha256 = ?",
        [name, sha256],
    ).fetchone()
    if row is None or row[1] == "replaced":
        raise LoadError(
            f"{name} on disk (sha256 {sha256[:8]}) has no current load_log row; "
            f"run `nrc download --month {month}` first"
        )
    others = [
        int(r[0])
        for r in con.execute(
            "SELECT load_id FROM load_log WHERE source_month = ? AND status = 'loaded' "
            "AND load_id <> ?",
            [month, int(row[0])],
        ).fetchall()
    ]
    return int(row[0]), str(row[1]), others


def stage(
    con: duckdb.DuckDBPyConnection, path: Path, month: str, file_schema: schema.FileSchema
) -> None:
    """Step 4: copy the month's Uber and Lyft rows into stg_trip, cast, with reason codes."""
    zones = [int(r[0]) for r in con.execute("SELECT zone_key FROM dim_zone").fetchall()]
    code_sql, detail_sql = rules.case_sql(month, zones)
    casts = ",\n    ".join(
        f"CAST({name if name in file_schema.present else 'NULL'} AS {kind.sql_type}) AS {name}"
        for name, kind in schema.TARGET
    )
    # Text form for duplicate detection: every source column, nulls with their own marker.
    hashed = ", ".join(f"coalesce(CAST({n} AS VARCHAR), chr(30))" for n in schema.TARGET_NAMES)
    con.execute(
        f"""
        CREATE OR REPLACE TABLE stg_trip AS
        WITH src AS (
            SELECT file_row_number AS src_row,
            {casts}
            FROM read_parquet({_quote(path)}, file_row_number = true)
            WHERE hvfhs_license_num IS NULL OR hvfhs_license_num IN ({_licenses(con)})
        )
        SELECT *,
            {code_sql} AS reason_code,
            {detail_sql} AS reason_detail,
            md5_number(concat_ws(chr(31), {hashed})) AS row_hash
        FROM src
        """
    )
    # Rule 7: among rows that passed rules 1 to 6, keep the lowest src_row of each copy group.
    con.execute(
        f"""
        UPDATE stg_trip SET reason_code = '{rules.DUPLICATE}', reason_detail = NULL
        WHERE src_row IN (
            SELECT s.src_row
            FROM stg_trip AS s
            JOIN (
                SELECT row_hash, min(src_row) AS keep_row
                FROM stg_trip WHERE reason_code IS NULL
                GROUP BY row_hash HAVING count(*) > 1
            ) AS d USING (row_hash)
            WHERE s.reason_code IS NULL AND s.src_row <> d.keep_row
        )
        """
    )


def _count_other_platform(con: duckdb.DuckDBPyConnection, path: Path) -> int:
    return _one(
        con,
        f"SELECT count(*) FROM read_parquet({_quote(path)}) "
        f"WHERE hvfhs_license_num IS NOT NULL AND hvfhs_license_num NOT IN ({_licenses(con)})",
    )


def verify(con: duckdb.DuckDBPyConnection, result: LoadResult) -> None:
    """Checks inside the write transaction. Raising here rolls the month back."""
    total = result.rows_kept + result.rows_quarantined + result.rows_other_platform
    if total != result.rows_in_file:
        raise LoadError(
            f"row_count {result.month}: kept {result.rows_kept} + quarantined "
            f"{result.rows_quarantined} + other platform {result.rows_other_platform} "
            f"!= rows in file {result.rows_in_file}"
        )
    trips = _one(
        con, "SELECT sum(trip_count) FROM fact_trip_hourly WHERE load_id = ?", [result.load_id]
    )
    if trips != result.rows_kept:
        raise LoadError(
            f"row_count {result.month}: fact trips {trips} != rows_kept {result.rows_kept}"
        )
    orphans = _one(
        con,
        """
        SELECT count(*) FROM fact_trip_hourly AS f
        WHERE f.load_id = ? AND (
            f.date_key NOT IN (SELECT date_key FROM dim_date)
            OR f.platform_key NOT IN (SELECT platform_key FROM dim_platform)
            OR f.pu_zone_key NOT IN (SELECT zone_key FROM dim_zone)
            OR f.do_zone_key NOT IN (SELECT zone_key FROM dim_zone))
        """,
        [result.load_id],
    )
    if orphans:
        raise LoadError(
            f"integrity {result.month}: {orphans} fact rows have a key missing from a dimension"
        )


def write(
    con: duckdb.DuckDBPyConnection,
    result: LoadResult,
    file_schema: schema.FileSchema,
    replace_ids: list[int],
    reload: bool,
) -> None:
    """Step 5: one transaction for the quarantine rows, fact rows and load_log counts."""
    month, load_id = result.month, result.load_id
    # Only fee parts the file has: a column missing from the whole file is not a null part.
    fee_parts = [c for c in rules.FEE_PARTS if c in file_schema.present]
    fee_null = " OR ".join(f"{c} IS NULL" for c in fee_parts) or "false"
    con.begin()
    try:
        if reload:
            low, high = _month_bounds(month)
            con.execute("DELETE FROM fact_trip_hourly WHERE date_key BETWEEN ? AND ?", [low, high])
            con.execute("DELETE FROM quarantine_trip WHERE source_month = ?", [month])
        con.execute(_sql("insert_quarantine.sql"), [load_id, month])
        con.execute(_sql("insert_fact.sql"), [load_id])
        result.rows_kept = _one(con, "SELECT count(*) FROM stg_trip WHERE reason_code IS NULL")
        result.rows_fee_part_null = _one(
            con, f"SELECT count(*) FROM stg_trip WHERE reason_code IS NULL AND ({fee_null})"
        )
        result.reasons = {code: 0 for code in rules.CODES}
        for code, n in con.execute(
            "SELECT reason_code, count(*) FROM quarantine_trip WHERE load_id = ? GROUP BY 1",
            [load_id],
        ).fetchall():
            result.reasons[str(code)] = int(n)
        result.rows_quarantined = sum(result.reasons.values())
        result.fact_rows = _one(
            con, "SELECT count(*) FROM fact_trip_hourly WHERE load_id = ?", [load_id]
        )
        verify(con, result)
        if replace_ids:
            con.execute(
                "UPDATE load_log SET status = 'replaced' WHERE list_contains(?, load_id)",
                [replace_ids],
            )
        con.execute(
            """
            UPDATE load_log SET status = 'loaded', loaded_at = ?, rows_in_file = ?,
                rows_other_platform = ?, rows_kept = ?, rows_quarantined = ?,
                rows_fee_part_null = ?, fact_rows = ?, extra_columns = ?, package_version = ?
            WHERE load_id = ?
            """,
            [
                datetime.now(UTC).replace(tzinfo=None),
                result.rows_in_file,
                result.rows_other_platform,
                result.rows_kept,
                result.rows_quarantined,
                result.rows_fee_part_null,
                result.fact_rows,
                list(file_schema.extra),
                __version__,
                load_id,
            ],
        )
        con.commit()
    except BaseException:
        con.rollback()
        raise


def load_month(
    month: str,
    paths: config.Paths,
    con: duckdb.DuckDBPyConnection,
    *,
    backfill: bool = False,
    free_fn: DiskFreeFn = disk_free,
) -> LoadResult:
    """Run steps 1 to 5 for one month. Safe to run again: a loaded file is skipped."""
    name = trip_file_name(month)
    path = paths.raw / name
    if not path.exists():
        raise LoadError(f"{path} is missing; run `nrc download --month {month}` first")
    if _one(con, "SELECT count(*) FROM dim_zone") == 0:
        raise LoadError("dim_zone is empty; run `nrc init` first")

    # Step 1: skip check.
    sha256 = sha256_file(path)
    load_id, status, others = _log_state(con, month, name, sha256)
    if status == "loaded" and not backfill:
        return LoadResult(month, "skipped", load_id)
    if others and not backfill:
        raise NeedsBackfillError(
            f"{month} is loaded from a different file; run `nrc load --backfill {month}`"
        )
    reload = backfill and (status == "loaded" or bool(others))

    # Step 2: disk check.
    paths.warehouse.mkdir(parents=True, exist_ok=True)
    need = config.LOAD_DISK_FACTOR * path.stat().st_size
    free = free_fn(paths.warehouse)
    if free < need:
        raise LoadDiskError(
            f"{paths.warehouse}: {free} bytes free, loading {name} needs {need} "
            f"({config.LOAD_DISK_FACTOR} times the file)"
        )

    # Step 3: schema check from the footer. Raises schema.SchemaError.
    file_schema = schema.check_file(path, month)

    # Steps 4 and 5.
    result = LoadResult(month, "reloaded" if reload else "loaded", load_id)
    result.rows_in_file = file_schema.rows
    result.rows_other_platform = _count_other_platform(con, path)
    try:
        stage(con, path, month, file_schema)
        write(con, result, file_schema, others, reload)
    finally:
        con.execute("DROP TABLE IF EXISTS stg_trip")
    con.execute("CHECKPOINT")
    if paths.db.exists():
        con.execute(
            "UPDATE load_log SET warehouse_bytes = ? WHERE load_id = ?",
            [paths.db.stat().st_size, load_id],
        )
    return result


def loadable_months(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> list[str]:
    """Study months with a current load_log row and a file on disk, in calendar order."""
    logged = {
        str(r[0])
        for r in con.execute(
            "SELECT DISTINCT source_month FROM load_log WHERE status <> 'replaced'"
        ).fetchall()
    }
    return [
        m for m in config.study_months() if m in logged and (paths.raw / trip_file_name(m)).exists()
    ]
