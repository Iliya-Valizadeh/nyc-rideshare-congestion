"""Small fake months and a fake zone lookup for tests and the demo (ADR 0004).

Everything here is made up with a fixed seed. It is not shaped like real trips and says
nothing about NYC. Files are written at run time into a folder the caller picks.
"""

from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.pipeline import schema
from nyc_rideshare_congestion.sources.tlc import record_download, sha256_file, trip_file_name

Row = dict[str, Any]

ZONE_COUNT = 265
BOROUGHS = ("Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island")
UNKNOWN_ZONE_ID = 999  # not in the fake lookup
OTHER_LICENSE = "HV0004"  # a real-looking license number outside the study


def write_zone_lookup(path: Path, zone_count: int = ZONE_COUNT) -> Path:
    """Write a lookup with the TLC header and made-up names. The last two rows mimic the
    lookup's "Unknown" and "Outside of NYC" rows; one row has a blank service zone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
        out.writerow(["LocationID", "Borough", "Zone", "service_zone"])
        for i in range(1, zone_count + 1):
            if i == zone_count - 1:
                out.writerow([i, "Unknown", "N/A", ""])
            elif i == zone_count:
                out.writerow([i, "N/A", "Outside of NYC", "N/A"])
            else:
                out.writerow([i, BOROUGHS[i % len(BOROUGHS)], f"Fake zone {i}", "Boro Zone"])
    return path


def month_start(month: str) -> datetime:
    return datetime.strptime(month + "-01", "%Y-%m-%d")


def trip(month: str, i: int = 0, **overrides: Any) -> Row:
    """One valid Uber trip in the month. `i` shifts the pickup by i minutes so rows differ."""
    pickup = month_start(month) + timedelta(days=1, minutes=i)
    row: Row = {
        "hvfhs_license_num": "HV0003",
        "dispatching_base_num": "B00001",
        "originating_base_num": "B00001",
        "request_datetime": pickup - timedelta(minutes=5),
        "on_scene_datetime": pickup - timedelta(minutes=1),
        "pickup_datetime": pickup,
        "dropoff_datetime": pickup + timedelta(seconds=600),
        "PULocationID": 10,
        "DOLocationID": 20,
        "trip_miles": 2.5,
        "trip_time": 600,
        "base_passenger_fare": 15.0,
        "tolls": 0.0,
        "bcf": 0.5,
        "sales_tax": 1.0,
        "congestion_surcharge": 2.75,
        "airport_fee": 0.0,
        "tips": 1.0,
        "driver_pay": 11.0,
        "shared_request_flag": "N",
        "shared_match_flag": "N",
        "access_a_ride_flag": "N",
        "wav_request_flag": "N",
        "wav_match_flag": "N",
        "cbd_congestion_fee": 1.5 if month >= config.FEE_COLUMN_FIRST_MONTH else 0.0,
    }
    row.update(overrides)
    return row


def bad_rows(month: str, start: int = 10_000) -> dict[str, Row]:
    """One row for each per-row rule (1 to 6), each failing only that rule."""
    before = month_start(month) - timedelta(hours=2)
    return {
        "NULL_REQUIRED": trip(month, start, DOLocationID=None),
        "PICKUP_OUTSIDE_MONTH": trip(
            month,
            start + 1,
            pickup_datetime=before,
            dropoff_datetime=before + timedelta(seconds=600),
        ),
        "UNKNOWN_ZONE": trip(month, start + 2, PULocationID=UNKNOWN_ZONE_ID),
        "NEGATIVE_FARE": trip(month, start + 3, tolls=-1.0),
        "NEGATIVE_DISTANCE": trip(month, start + 4, trip_miles=-0.5),
        "IMPOSSIBLE_DURATION": trip(month, start + 5, trip_time=0),
    }


def month_rows(month: str, n: int = 200, seed: int = 0) -> list[Row]:
    """A few hundred made-up rows: random valid trips for both platforms, one bad row
    per rule, two exact copies, one near copy, and three other-platform rows."""
    rng = random.Random(f"{seed}-{month}")
    rows = []
    for _ in range(n):
        seconds = rng.randint(120, 3600)
        pickup = month_start(month) + timedelta(
            days=rng.randint(0, 27), hours=rng.randint(0, 23), minutes=rng.randint(0, 59)
        )
        rows.append(
            trip(
                month,
                hvfhs_license_num=rng.choice(("HV0003", "HV0005")),
                pickup_datetime=pickup,
                dropoff_datetime=pickup + timedelta(seconds=seconds),
                PULocationID=rng.randint(1, ZONE_COUNT),
                DOLocationID=rng.randint(1, ZONE_COUNT),
                trip_miles=round(rng.uniform(0.2, 20.0), 2),
                trip_time=seconds,
                base_passenger_fare=round(rng.uniform(5.0, 80.0), 2),
            )
        )
    rows += list(bad_rows(month).values())
    rows += [dict(rows[0]), dict(rows[0])]  # exact copies: one kept, two quarantined
    rows.append({**rows[1], "tips": rows[1]["tips"] + 1.0})  # near copy: both kept
    rows += [trip(month, 20_000 + k, hvfhs_license_num=OTHER_LICENSE) for k in range(3)]
    return rows


def full_month_rows(month: str, extra: int = 0, seed: int = 0) -> list[Row]:
    """Valid rows only: one trip per day for each platform, so every day is covered,
    plus `extra` random valid trips. No bad rows, copies or other platforms."""
    rng = random.Random(f"full-{seed}-{month}")
    start = month_start(month)
    days = ((start + timedelta(days=32)).replace(day=1) - start).days
    rows = []
    for day in range(days):
        for k, license_num in enumerate(("HV0003", "HV0005")):
            pickup = start + timedelta(days=day, hours=rng.randint(0, 23), minutes=k)
            rows.append(
                trip(
                    month,
                    hvfhs_license_num=license_num,
                    pickup_datetime=pickup,
                    dropoff_datetime=pickup + timedelta(seconds=600),
                    PULocationID=rng.randint(1, ZONE_COUNT),
                    DOLocationID=rng.randint(1, ZONE_COUNT),
                )
            )
    for i in range(extra):
        pickup = start + timedelta(days=rng.randint(0, days - 1), hours=rng.randint(0, 23))
        rows.append(
            trip(
                month,
                hvfhs_license_num=rng.choice(("HV0003", "HV0005")),
                pickup_datetime=pickup + timedelta(seconds=i),
                dropoff_datetime=pickup + timedelta(seconds=i + 600),
                PULocationID=rng.randint(1, ZONE_COUNT),
                DOLocationID=rng.randint(1, ZONE_COUNT),
            )
        )
    return rows


def write_month(
    path: Path,
    rows: list[Row],
    *,
    text: pa.DataType | None = None,
    zone: pa.DataType | None = None,
    fee: bool = True,
    drop: tuple[str, ...] = (),
) -> Path:
    """Write rows as one TLC-style Parquet file. `text` and `zone` pick the source types
    (ADR 0001 found string/int64 and large_string/int32); `fee=False` leaves out
    cbd_congestion_fee; `drop` leaves out other columns."""
    types = {
        "VARCHAR": text or pa.large_string(),
        "INTEGER": zone or pa.int32(),
        "BIGINT": pa.int64(),
        "TIMESTAMP": pa.timestamp("us"),
        "DOUBLE": pa.float64(),
    }
    fields = [
        pa.field(name, types[kind.sql_type])
        for name, kind in schema.TARGET
        if name not in drop and (fee or name != schema.FEE_COLUMN)
    ]
    arrow_schema = pa.schema(fields)
    table = pa.table({f.name: [r.get(f.name) for r in rows] for f in fields}, schema=arrow_schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)
    return path


def place_month(
    paths: config.Paths,
    con: duckdb.DuckDBPyConnection,
    month: str,
    rows: list[Row],
    **kwargs: Any,
) -> Path:
    """Write a fake month into data/raw/ and log it the way `nrc download` would."""
    path = write_month(paths.raw / trip_file_name(month), rows, **kwargs)
    record_download(con, month, path.name, sha256_file(path), path.stat().st_size, None)
    return path
