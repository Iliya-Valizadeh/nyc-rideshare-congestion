"""The target schema and the whole-file schema check (ADR 0004).

The check reads only the Parquet footer, so it runs before any row is read. A failure
stops the month with exit code 2 and writes nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from nyc_rideshare_congestion import config

FEE_COLUMN = "cbd_congestion_fee"


def _is_text(t: pa.DataType) -> bool:
    return bool(pa.types.is_string(t) or pa.types.is_large_string(t))


def _is_int(t: pa.DataType) -> bool:
    return bool(pa.types.is_int32(t) or pa.types.is_int64(t))


def _is_float(t: pa.DataType) -> bool:
    return bool(pa.types.is_float64(t) or pa.types.is_float32(t))


def _is_timestamp(t: pa.DataType) -> bool:
    return bool(pa.types.is_timestamp(t))


@dataclass(frozen=True)
class Kind:
    sql_type: str
    allowed: str
    test: Callable[[pa.DataType], bool]


TEXT = Kind("VARCHAR", "string or large_string", _is_text)
ZONE = Kind("INTEGER", "int32 or int64", _is_int)
SECONDS = Kind("BIGINT", "int32 or int64", _is_int)
TIME = Kind("TIMESTAMP", "timestamp in any unit", _is_timestamp)
NUMBER = Kind("DOUBLE", "double or float", _is_float)

# The 25 source columns from ADR 0001, in file order, with their target kinds.
TARGET: tuple[tuple[str, Kind], ...] = (
    ("hvfhs_license_num", TEXT),
    ("dispatching_base_num", TEXT),
    ("originating_base_num", TEXT),
    ("request_datetime", TIME),
    ("on_scene_datetime", TIME),
    ("pickup_datetime", TIME),
    ("dropoff_datetime", TIME),
    ("PULocationID", ZONE),
    ("DOLocationID", ZONE),
    ("trip_miles", NUMBER),
    ("trip_time", SECONDS),
    ("base_passenger_fare", NUMBER),
    ("tolls", NUMBER),
    ("bcf", NUMBER),
    ("sales_tax", NUMBER),
    ("congestion_surcharge", NUMBER),
    ("airport_fee", NUMBER),
    ("tips", NUMBER),
    ("driver_pay", NUMBER),
    ("shared_request_flag", TEXT),
    ("shared_match_flag", TEXT),
    ("access_a_ride_flag", TEXT),
    ("wav_request_flag", TEXT),
    ("wav_match_flag", TEXT),
    (FEE_COLUMN, NUMBER),
)
TARGET_NAMES = tuple(name for name, _ in TARGET)


class SchemaError(ValueError):
    """A whole-file rule failed. `code` is SCHEMA_MISSING_COLUMN or SCHEMA_BAD_TYPE."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class FileSchema:
    rows: int
    present: tuple[str, ...]  # target columns found in the file
    extra: tuple[str, ...]  # source columns the pipeline ignores


def check_file(path: Path, month: str) -> FileSchema:
    """Check the footer of one monthly file against the target schema."""
    footer = pq.ParquetFile(path)
    arrow = footer.schema_arrow
    found = {field.name: field.type for field in arrow}
    missing = [
        name
        for name in TARGET_NAMES
        if name not in found and not (name == FEE_COLUMN and month < config.FEE_COLUMN_FIRST_MONTH)
    ]
    if missing:
        raise SchemaError(
            "SCHEMA_MISSING_COLUMN", f"{path.name} has no column {', '.join(missing)}"
        )
    for name, kind in TARGET:
        if name in found and not kind.test(found[name]):
            raise SchemaError(
                "SCHEMA_BAD_TYPE",
                f"{path.name} column {name} is {found[name]}, expected {kind.allowed}",
            )
    return FileSchema(
        rows=footer.metadata.num_rows,
        present=tuple(n for n in TARGET_NAMES if n in found),
        extra=tuple(n for n in found if n not in TARGET_NAMES),
    )
