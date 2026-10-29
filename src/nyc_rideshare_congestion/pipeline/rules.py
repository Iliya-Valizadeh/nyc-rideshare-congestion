"""The row rules and their order (ADR 0004). The load, the docs and the tests read this list.

A row gets the code of the first rule it fails, so each row has exactly one code. Each
rule is a list of checks. A check is a SQL condition on the staged row, plus the column
to report in `reason_detail` (rules 1, 3 and 4 only).
"""

from __future__ import annotations

from dataclasses import dataclass

from nyc_rideshare_congestion import config

REQUIRED = (
    "hvfhs_license_num",
    "pickup_datetime",
    "dropoff_datetime",
    "PULocationID",
    "DOLocationID",
    "trip_miles",
    "trip_time",
    "base_passenger_fare",
)
# Parts of the rider price. A null part counts as zero and is not quarantined.
FEE_PARTS = (
    "tolls",
    "bcf",
    "sales_tax",
    "congestion_surcharge",
    "airport_fee",
    "cbd_congestion_fee",
)
MONEY = ("base_passenger_fare", *FEE_PARTS)

# Placeholders filled per month by `case_sql`.
MONTH_START = "{month_start}"
NEXT_MONTH = "{next_month}"
ZONES = "{zones}"


@dataclass(frozen=True)
class Check:
    condition: str
    detail: str | None = None


@dataclass(frozen=True)
class Rule:
    order: int
    code: str
    description: str
    checks: tuple[Check, ...]


_MAX = config.MAX_TRIP_SECONDS

RULES: tuple[Rule, ...] = (
    Rule(
        1,
        "NULL_REQUIRED",
        "A column the fact table needs is null: " + ", ".join(REQUIRED),
        tuple(Check(f"{c} IS NULL", c) for c in REQUIRED),
    ),
    Rule(
        2,
        "PICKUP_OUTSIDE_MONTH",
        "pickup_datetime is not in the file's month",
        (
            Check(
                f"pickup_datetime < TIMESTAMP '{MONTH_START}' "
                f"OR pickup_datetime >= TIMESTAMP '{NEXT_MONTH}'"
            ),
        ),
    ),
    Rule(
        3,
        "UNKNOWN_ZONE",
        "PULocationID or DOLocationID is not a zone_key in dim_zone",
        (
            Check(f"PULocationID NOT IN ({ZONES})", "PULocationID"),
            Check(f"DOLocationID NOT IN ({ZONES})", "DOLocationID"),
        ),
    ),
    Rule(
        4,
        "NEGATIVE_FARE",
        "A fare or fee column is below zero: " + ", ".join(MONEY),
        tuple(Check(f"{c} < 0", c) for c in MONEY),
    ),
    Rule(5, "NEGATIVE_DISTANCE", "trip_miles is below zero", (Check("trip_miles < 0"),)),
    Rule(
        6,
        "IMPOSSIBLE_DURATION",
        f"trip_time is zero or less, dropoff is not after pickup, or either length is "
        f"over {_MAX} seconds",
        (
            Check(
                f"trip_time <= 0 OR trip_time > {_MAX} "
                "OR dropoff_datetime <= pickup_datetime "
                f"OR dropoff_datetime > pickup_datetime + INTERVAL {_MAX} SECOND"
            ),
        ),
    ),
    # Rule 7 compares rows with each other, so it has no per-row condition. The load
    # applies it after rules 1 to 6 (see load.py).
    Rule(
        7,
        "DUPLICATE_TRIP",
        "An exact copy of an earlier row in the same file that passed rules 1 to 6",
        (),
    ),
)
CODES = tuple(rule.code for rule in RULES)
DUPLICATE = "DUPLICATE_TRIP"


def next_month(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return f"{year:04d}-{mon:02d}"


def case_sql(month: str, zone_keys: list[int]) -> tuple[str, str]:
    """Return the SQL CASE expressions for reason_code and reason_detail (rules 1 to 6)."""
    if not zone_keys:
        raise ValueError("dim_zone is empty; run `nrc init` first")
    fill = {
        "month_start": f"{month}-01 00:00:00",
        "next_month": f"{next_month(month)}-01 00:00:00",
        "zones": ", ".join(str(int(z)) for z in sorted(zone_keys)),
    }
    code_parts, detail_parts = [], []
    for rule in RULES:
        for check in rule.checks:
            cond = check.condition.format(**fill)
            code_parts.append(f"WHEN {cond} THEN '{rule.code}'")
            detail = "NULL" if check.detail is None else f"'{check.detail}'"
            detail_parts.append(f"WHEN {cond} THEN {detail}")
    return (
        "CASE " + " ".join(code_parts) + " END",
        "CASE " + " ".join(detail_parts) + " END",
    )
