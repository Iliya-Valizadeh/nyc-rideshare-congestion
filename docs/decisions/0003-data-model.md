# 0003: Data model

Date: 2026-10-21. Status: accepted.

## Context

The spec asks for a star schema with `fact_trip`, or `fact_trip_hourly` if size forces
it, plus `dim_zone`, `dim_date` and `dim_platform`, with keys and constraints. ADR 0002
chose DuckDB, in one file under `data/warehouse/`, with a 2 GB memory cap.

Facts that shape the grain:

- The source is 13.05 GB of compressed Parquet over 27 monthly files (ADR 0001). Each
  row is one trip. A table with one row per trip would hold as many rows as the source,
  and the warehouse would be a second full copy of it.
- The files have no trip ID. A primary key on single trips would need a made-up key,
  such as a hash of the whole row, with one index entry per trip.
- Nothing in the study plan (`docs/eval_plan.md`) needs single trips. Its unit is one
  drop-off zone, on one day, for one platform. Its trip rules need the pickup zone, and
  its outcomes need sums of fares, prices, fee, trip time and miles.
- The spec's transforms need the hour (hourly demand), the platform (platform share)
  and the zone, fares and miles (fare per mile by zone).
- A throwaway test on this machine (synthetic keys, DuckDB 1.5.6, not committed) put
  the same five-column primary key as below on tens of millions of rows under a 1 GB
  cap. It worked. Adding foreign keys from the fact table to three dimension tables
  made inserts several times slower and the file several times larger. DuckDB builds
  an index for every foreign key, and it blocks deletes and updates on rows that are
  referenced. The test's numbers are not used as claims.

## Options

- `fact_trip`, one row per trip. Most flexible. Costs a second full copy of the data,
  a made-up trip key, and an index entry per trip. Every panel build would scan every
  trip. Rejected: the 2 GB cap and the study do not justify it.
- `fact_trip_hourly`, one row per pickup hour, platform, pickup zone and drop-off zone.
  It keeps everything the plan and the transforms use. It has at most one row per trip,
  and fewer wherever two trips share a cell. Its natural key fits a real primary key.
- A daily fact (no hour). Smaller, but it loses hourly demand, which the spec asks for.
  Kept as the fallback below.

## Decision

Use `fact_trip_hourly`. The reason in one line: it is the smallest table that still
answers every question in the plan and the spec, and its natural key can carry a
primary key within the memory cap.

### Grain

One row per (pickup date, pickup hour, platform, pickup zone, drop-off zone), for kept
trips only. The date and hour come from `pickup_datetime`, in the local time the TLC
files use. A trip belongs to the file month of its pickup (ADR 0004 quarantines the
rest), so each trip lands in exactly one month.

### Tables

All DDL lives in `sql/schema/`, one file per table, run in name order by the
`init` command. The SQL uses standard types and standard constraint syntax where
DuckDB allows it, so a later PostgreSQL port stays possible (ADR 0002).

| File | Table |
|---|---|
| `sql/schema/01_load_log.sql` | `load_log` (defined in ADR 0004) |
| `sql/schema/02_dim_platform.sql` | `dim_platform`, with its two seed rows |
| `sql/schema/03_dim_zone.sql` | `dim_zone` |
| `sql/schema/04_dim_date.sql` | `dim_date` |
| `sql/schema/05_fact_trip_hourly.sql` | `fact_trip_hourly` |
| `sql/schema/06_quarantine_trip.sql` | `quarantine_trip` (defined in ADR 0004) |

`dim_platform`:

| Column | Type | Rule |
|---|---|---|
| `platform_key` | `TINYINT` | Primary key |
| `license_num` | `VARCHAR` | `NOT NULL`, `UNIQUE`, `CHECK` in (`HV0003`, `HV0005`) |
| `platform_name` | `VARCHAR` | `NOT NULL`, `UNIQUE` |

Seed rows: `(1, 'HV0003', 'Uber')` and `(2, 'HV0005', 'Lyft')`, from the data
dictionary (ADR 0001).

`dim_zone`, loaded from `taxi_zone_lookup.csv`:

| Column | Type | Rule |
|---|---|---|
| `zone_key` | `SMALLINT` | Primary key, the TLC `LocationID`, `CHECK (zone_key > 0)` |
| `borough` | `VARCHAR` | `NOT NULL` |
| `zone_name` | `VARCHAR` | `NOT NULL` |
| `service_zone` | `VARCHAR` | Nullable (the lookup has blanks) |
| `in_congestion_zone` | `BOOLEAN` | `NOT NULL`, true for the MTA list in ADR 0001 |

`in_congestion_zone` comes from `cbd_zones` in `reports/source_manifest.json`, the
committed copy of the MTA list. A test checks that it equals `treated_zones` in the
plan's parameter block. Zones `264` and `265` ("Unknown" and "Outside of NYC") are real
rows in the lookup, so they are valid keys. The plan needs them to measure the unknown
drop-off share. Zone loads only insert. If a later lookup renames or drops a zone, the
load fails with a message and a new ADR decides.

`dim_date`, made by SQL from a date range in the config (2023-01-01 to 2025-12-31):

| Column | Type | Rule |
|---|---|---|
| `date_key` | `INTEGER` | Primary key, `yyyymmdd` |
| `full_date` | `DATE` | `NOT NULL`, `UNIQUE` |
| `year`, `month`, `day` | `SMALLINT`, `TINYINT`, `TINYINT` | `NOT NULL` |
| `month_key` | `INTEGER` | `NOT NULL`, `yyyymm` |
| `iso_day_of_week` | `TINYINT` | `NOT NULL`, `CHECK` 1 to 7 |
| `iso_week` | `TINYINT` | `NOT NULL` |
| `is_weekend` | `BOOLEAN` | `NOT NULL` |

Holidays and excluded days stay in the plan's parameter block, not in `dim_date`. The
plan is the one place that defines them.

`fact_trip_hourly`:

| Column | Type | Rule |
|---|---|---|
| `date_key` | `INTEGER` | `NOT NULL`, foreign key to `dim_date` |
| `pickup_hour` | `TINYINT` | `NOT NULL`, `CHECK` 0 to 23 | <!-- not-a-claim -->
| `platform_key` | `TINYINT` | `NOT NULL`, foreign key to `dim_platform` |
| `pu_zone_key` | `SMALLINT` | `NOT NULL`, foreign key to `dim_zone` |
| `do_zone_key` | `SMALLINT` | `NOT NULL`, foreign key to `dim_zone` |
| `trip_count` | `INTEGER` | `NOT NULL`, `CHECK (trip_count > 0)` |
| `trip_miles_sum` | `DOUBLE` | `NOT NULL`, `CHECK (>= 0)` |
| `trip_time_sum_s` | `BIGINT` | `NOT NULL`, `CHECK (> 0)`, seconds |
| `base_fare_sum` | `DOUBLE` | `NOT NULL`, `CHECK (>= 0)`, sum of `base_passenger_fare` |
| `rider_price_sum` | `DOUBLE` | `NOT NULL`, `CHECK (>= 0)` |
| `cbd_fee_sum` | `DOUBLE` | `NOT NULL`, `CHECK (>= 0)`, zero before 2025 |
| `cbd_fee_trip_count` | `INTEGER` | `NOT NULL`, `CHECK` 0 to `trip_count`, trips with fee above zero |
| `load_id` | `INTEGER` | `NOT NULL`, the `load_log` row that wrote it |

Primary key: `(date_key, pickup_hour, platform_key, pu_zone_key, do_zone_key)`.

Rider price per trip is the plan's formula: `base_passenger_fare + tolls + bcf +
sales_tax + congestion_surcharge + airport_fee + cbd_congestion_fee`. A missing or null
fee part counts as zero (ADR 0004 counts how often). All parts are zero or more after
validation, which is why the `CHECK` on `rider_price_sum` holds. It does not check
`rider_price_sum >= base_fare_sum`, because two sums of doubles added in a different
order can differ in the last digits. Means come from sums
divided by `trip_count`, so they are trip-weighted, as the plan says.

### Keys and references

- Every table has a primary key. Natural keys that must be unique also carry `UNIQUE`.
- The fact table does not declare `FOREIGN KEY` constraints. The throwaway test above
  showed their cost, and DuckDB would block the backfill deletes in ADR 0004. The
  references are enforced in two places instead:
  - The load joins to the dimension tables and fails the whole month, with a rollback,
    if any fact row has a key that is missing from a dimension.
  - The `check` command (ADR 0004) counts orphan keys and fails if any exist. The plan
    lists this check before analysis.
- This is a real gap against a database that enforces foreign keys. The README and
  `docs/whats_weak.md` must say so in plain words.

### The index for the timing task (T07)

DuckDB keeps min and max values for each block of rows, which already speeds up range
scans on columns that follow load order, such as `date_key`. An index helps most for a
selective filter on a column that does not follow load order. So T07 times this query
before and after `CREATE INDEX ix_fact_do_zone ON fact_trip_hourly (do_zone_key)`:
daily trips for one drop-off zone over the whole range. T07 reports the result even if
the gain is small. The primary key index is not the one being timed.

### Fallback if the hourly fact is too big

T06 records the fact rows and the warehouse file size after each month in
`reports/load_summary.json`. If a month cannot load within the memory cap or the
10-minute command limit, T06 stops and reports it. The grain does not change in code
until a new ADR replaces this one. The daily fact, with the same columns and no
`pickup_hour`, is the planned replacement. Hourly demand would then come from a
separate small table by pickup zone and hour.

## Consequences

- All study and transform queries read a table smaller than the trips, never the raw
  files.
- A question that needs single trips (for example, the spread of fares inside one
  cell) cannot be answered from the warehouse. It would need a new pass over
  `data/raw/`.
- Uniqueness of trips rests on the load rules in ADR 0004 (duplicates quarantined, one
  month per file, a month loaded once), not on a trip-level key.
- Disk: each month needs room for its raw file, a staging copy and spill files. ADR
  0004 sets the free-space check before each load.
