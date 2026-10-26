"""Tests for the DuckDB connection settings (ADR 0004 test 13) and the load_log DDL."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.pipeline import db


def _setting(con: duckdb.DuckDBPyConnection, name: str) -> str:
    row = con.execute(f"SELECT current_setting('{name}')").fetchone()
    assert row is not None
    return str(row[0])


def test_connect_sets_the_four_settings(tmp_path: Path) -> None:
    con = db.connect(tmp_path / "w" / "x.duckdb", tmp_path / "spill", memory_limit_mb=1000)
    try:
        assert _setting(con, "threads") == str(config.DUCKDB_THREADS)
        assert _setting(con, "preserve_insertion_order").lower() == "false"
        assert Path(_setting(con, "temp_directory")) == tmp_path / "spill"
        assert _setting(con, "memory_limit") == "953.6 MiB"  # 1000 MB in DuckDB's units
    finally:
        con.close()
    default = db.connect(":memory:", tmp_path / "spill")
    try:
        assert _setting(default, "memory_limit") == "1.8 GiB"  # 2000 MB
    finally:
        default.close()


@pytest.mark.parametrize("mb", [0, -1, config.MAX_MEMORY_MB + 1, 8000])
def test_connect_refuses_bad_memory_limits(tmp_path: Path, mb: int) -> None:
    with pytest.raises(ValueError):
        db.connect(":memory:", tmp_path, memory_limit_mb=mb)


def test_schema_runs_twice_and_enforces_keys(tmp_path: Path) -> None:
    con = db.connect(":memory:", tmp_path)
    try:
        assert "01_load_log.sql" in db.run_schema(con)
        db.run_schema(con)
        insert = (
            "INSERT INTO load_log (file_name, sha256, source_month, size_bytes, status, "
            "downloaded_at) VALUES (?, ?, ?, 1, ?, now())"
        )
        con.execute(insert, ["f", "a" * 64, "2024-12", "downloaded"])
        with pytest.raises(duckdb.ConstraintException):
            con.execute(insert, ["f", "a" * 64, "2024-12", "downloaded"])
        with pytest.raises(duckdb.ConstraintException):
            con.execute(insert, ["g", "a" * 64, "2024-12", "unknown"])
        with pytest.raises(duckdb.ConstraintException):
            con.execute(insert, ["g", "not a hash", "2024-12", "downloaded"])
    finally:
        con.close()
