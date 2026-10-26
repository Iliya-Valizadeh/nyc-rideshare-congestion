"""The one place that opens DuckDB, with the memory and thread limits from ADR 0004."""

from __future__ import annotations

from pathlib import Path

import duckdb

from nyc_rideshare_congestion import config


def connect(
    db_path: Path | str, temp_dir: Path, memory_limit_mb: int = config.MAX_MEMORY_MB
) -> duckdb.DuckDBPyConnection:
    """Open the warehouse with a memory cap, two threads and a spill folder."""
    if not 0 < memory_limit_mb <= config.MAX_MEMORY_MB:
        raise ValueError(
            f"memory_limit_mb must be between 1 and {config.MAX_MEMORY_MB}, got {memory_limit_mb}"
        )
    temp_dir.mkdir(parents=True, exist_ok=True)
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(
        str(db_path),
        config={
            "memory_limit": f"{memory_limit_mb}MB",
            "threads": config.DUCKDB_THREADS,
            "temp_directory": str(temp_dir),
            "preserve_insertion_order": False,
        },
    )


def run_schema(con: duckdb.DuckDBPyConnection, schema_dir: Path | None = None) -> list[str]:
    """Run every DDL file in sql/schema/ in name order. Each file is safe to run twice."""
    folder = schema_dir or config.SQL_DIR / "schema"
    files = sorted(folder.glob("*.sql"))
    for path in files:
        con.execute(path.read_text(encoding="utf-8"))
    return [p.name for p in files]
