"""Build the transform tables from sql/transforms/ (ADR 0007).

Each file makes one `tf_*` table from the fact and dimension tables with
`CREATE OR REPLACE`, so running the step twice gives the same tables.
"""

from __future__ import annotations

import re
from pathlib import Path

import duckdb

from nyc_rideshare_congestion import config

TRANSFORM_SQL = config.SQL_DIR / "transforms"
_TABLE = re.compile(r"CREATE OR REPLACE TABLE (\w+)", re.IGNORECASE)


class TransformError(RuntimeError):
    """The warehouse has no fact rows to transform."""


def table_name(path: Path) -> str:
    """The table a transform file creates, read from its CREATE statement."""
    match = _TABLE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise ValueError(f"{path.name}: no CREATE OR REPLACE TABLE statement")
    return match.group(1)


def run_transforms(con: duckdb.DuckDBPyConnection, folder: Path = TRANSFORM_SQL) -> dict[str, int]:
    """Run every transform file in name order. Returns rows per table built."""
    row = con.execute("SELECT count(*) FROM fact_trip_hourly").fetchone()
    if row is None or row[0] == 0:
        raise TransformError("fact_trip_hourly is empty; run `nrc load` first")
    counts: dict[str, int] = {}
    for path in sorted(folder.glob("*.sql")):
        name = table_name(path)
        con.execute(path.read_text(encoding="utf-8"))
        got = con.execute(f"SELECT count(*) FROM {name}").fetchone()
        counts[name] = int(got[0]) if got else 0
    return counts
