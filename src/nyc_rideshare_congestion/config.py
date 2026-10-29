"""Paths, months, URLs and limits. Other modules take their numbers from here."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# Sources (ADR 0001).
TRIP_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_{month}.parquet"
TRIP_FILE = "fhvhv_tripdata_{month}.parquet"
ZONE_LOOKUP_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
ZONE_SHAPES_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip"
CBD_ZONES_URL = "https://data.ny.gov/resource/yfdc-w5jh.json?$select=taxi_zone&$order=taxi_zone"
REFERENCE_FILES = {
    "taxi_zone_lookup.csv": ZONE_LOOKUP_URL,
    "taxi_zones.zip": ZONE_SHAPES_URL,
}

# Months in the study (ADR 0001), and the download order: the study windows first,
# then the same months a year earlier, then the rest.
FIRST_MONTH = "2023-01"
LAST_MONTH = "2025-03"
PRIORITY_RANGES = (("2024-10", "2025-03"), ("2023-10", "2024-03"))

# Warehouse (ADR 0003 and 0004).
DATE_FIRST = "2023-01-01"  # dim_date range, both ends included
DATE_LAST = "2025-12-31"
FEE_COLUMN_FIRST_MONTH = "2025-01"  # cbd_congestion_fee may be missing before this month
MAX_TRIP_SECONDS = 6 * 60 * 60  # rule IMPOSSIBLE_DURATION: longer trips are clock errors
LOAD_DISK_FACTOR = 3  # free space a load needs, as a multiple of the raw file size

# The check command (ADR 0004 and 0007).
ROWS_KEPT_BAND = (0.5, 1.5)  # each month's rows_kept, as a share of the median month
MTIME_TOLERANCE_S = 2.0  # raw file modified time vs the logged Last-Modified

# Index timing (ADR 0007).
TIMING_RUNS = 5  # timed runs per query, after one untimed warm-up run
TIMING_SLICE_MONTHS = 3  # months copied for the slice timing, newest first
TIMING_SCRATCH_DB = "index_timing.duckdb"  # scratch file in data/tmp/, removed after
INDEX_TIMING_REPORT = "index_timing.json"

# Limits.
DISK_HEADROOM = 0.2  # free space must be the file size plus this share
HASH_CHUNK_BYTES = 1024 * 1024  # SHA-256 reads the file in 1 MiB chunks
HTTP_TIMEOUT_S = 60
MAX_MEMORY_MB = 2000  # DuckDB memory cap ("2GB"); connect() refuses more
DUCKDB_THREADS = 2

# Exit codes (ADR 0004).
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_ARGS = 2
EXIT_SCHEMA = 2
EXIT_NEEDS_BACKFILL = 3
EXIT_DISK = 4

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
SQL_DIR = REPO_ROOT / "sql"
SOURCE_MANIFEST = REPO_ROOT / "reports" / "source_manifest.json"
EVAL_PLAN = REPO_ROOT / "docs" / "eval_plan.md"
ZONE_LOOKUP_FILE = "taxi_zone_lookup.csv"


def months(first: str, last: str) -> list[str]:
    """Return every month from first to last, both included, as YYYY-MM."""
    year, month = (int(x) for x in first.split("-"))
    end = tuple(int(x) for x in last.split("-"))
    if (year, month) > end:
        raise ValueError(f"first month {first} is after last month {last}")
    out = []
    while (year, month) <= end:
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def study_months() -> list[str]:
    """Every month the study downloads and loads, in calendar order."""
    return months(FIRST_MONTH, LAST_MONTH)


def download_order() -> list[str]:
    """The study months in the order ADR 0001 sets for downloads."""
    order: list[str] = []
    for first, last in PRIORITY_RANGES:
        order += [m for m in months(first, last) if m not in order]
    return order + [m for m in study_months() if m not in order]


@dataclass(frozen=True)
class Paths:
    """Where data, the warehouse and reports live, under one root folder."""

    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def replaced(self) -> Path:
        return self.raw / "replaced"

    @property
    def warehouse(self) -> Path:
        return self.root / "data" / "warehouse"

    @property
    def db(self) -> Path:
        return self.warehouse / "warehouse.duckdb"

    @property
    def tmp(self) -> Path:
        return self.root / "data" / "tmp"

    @property
    def reports(self) -> Path:
        return self.root / "reports"
