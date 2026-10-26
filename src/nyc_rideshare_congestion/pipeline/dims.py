"""Fill dim_date and dim_zone (ADR 0003). dim_platform is seeded by its DDL file."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.pipeline import db


class ZoneChangeError(RuntimeError):
    """The zone lookup renamed or dropped a zone that is already loaded."""


def cbd_zone_ids(manifest: Path = config.SOURCE_MANIFEST) -> list[int]:
    """The MTA congestion-zone list, from the committed source manifest (ADR 0001)."""
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return sorted(int(z) for z in data["cbd_zones"]["ids"])


def fill_dates(
    con: duckdb.DuckDBPyConnection, first: str = config.DATE_FIRST, last: str = config.DATE_LAST
) -> int:
    """Insert every day from first to last that is not there yet. Returns rows in dim_date."""
    con.execute(
        """
        INSERT OR IGNORE INTO dim_date
        SELECT
            CAST(strftime(d, '%Y%m%d') AS INTEGER),
            CAST(d AS DATE),
            year(d), month(d), day(d),
            year(d) * 100 + month(d),
            isodow(d),
            week(d),
            isodow(d) >= 6
        FROM generate_series(CAST(? AS DATE), CAST(? AS DATE), INTERVAL 1 DAY) AS t(d)
        """,
        [first, last],
    )
    row = con.execute("SELECT count(*) FROM dim_date").fetchone()
    return 0 if row is None else int(row[0])


def load_zones(con: duckdb.DuckDBPyConnection, lookup_csv: Path, cbd_ids: list[int]) -> int:
    """Insert zones from the TLC lookup. Fails if a loaded zone was renamed or dropped."""
    escaped = str(lookup_csv).replace("'", "''")
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE stg_zone AS
        SELECT
            CAST(LocationID AS SMALLINT) AS zone_key,
            Borough AS borough,
            Zone AS zone_name,
            NULLIF(trim(service_zone), '') AS service_zone,
            list_contains(?::SMALLINT[], CAST(LocationID AS SMALLINT)) AS in_congestion_zone
        FROM read_csv('{escaped}', header = true, all_varchar = true)
        """,
        [cbd_ids],
    )
    changed = con.execute(
        """
        SELECT d.zone_key FROM dim_zone d
        LEFT JOIN stg_zone s USING (zone_key)
        WHERE s.zone_key IS NULL
           OR d.borough IS DISTINCT FROM s.borough
           OR d.zone_name IS DISTINCT FROM s.zone_name
           OR d.service_zone IS DISTINCT FROM s.service_zone
           OR d.in_congestion_zone IS DISTINCT FROM s.in_congestion_zone
        ORDER BY 1
        """
    ).fetchall()
    if changed:
        keys = ", ".join(str(r[0]) for r in changed)
        raise ZoneChangeError(
            f"the zone lookup changed or dropped loaded zones {keys}; a new ADR must decide"
        )
    con.execute("INSERT OR IGNORE INTO dim_zone SELECT * FROM stg_zone")
    con.execute("DROP TABLE stg_zone")
    row = con.execute("SELECT count(*) FROM dim_zone").fetchone()
    return 0 if row is None else int(row[0])


def init_warehouse(
    con: duckdb.DuckDBPyConnection, lookup_csv: Path, cbd_ids: list[int] | None = None
) -> dict[str, int]:
    """Run the DDL, fill dim_date and load dim_zone. Safe to run twice."""
    db.run_schema(con)
    dates = fill_dates(con)
    zones = load_zones(con, lookup_csv, cbd_zone_ids() if cbd_ids is None else cbd_ids)
    row = con.execute("SELECT count(*) FROM dim_platform").fetchone()
    return {"dim_date": dates, "dim_zone": zones, "dim_platform": 0 if row is None else int(row[0])}
