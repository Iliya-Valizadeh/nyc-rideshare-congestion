# 0004: Loads, data quality and the pipeline layout

Date: 2026-10-21. Status: accepted.

## Context

The spec asks for loads that can run twice without making duplicates, a
`--backfill YYYY-MM` option, row checks with a quarantine table and a reason code per
row, a freshness and row-count check that fails loudly, and tests for malformed rows, a
missing column, duplicate events and a re-run. The study plan (`docs/eval_plan.md`)
adds pipeline checks that must pass before any analysis. ADR 0003 sets the tables.

Limits from the project plan: 2 GB of memory for the pipeline, a 10-minute limit per command,
no network in tests or the demo, and no scheduled jobs.

## Options

- Load in pandas or Polars, one month at a time. A full month of trips does not fit in
  2 GB in pandas. Rejected.
- Do every step in DuckDB SQL, reading each Parquet file straight from disk, with
  Python only running the steps and checking the file's schema from its footer. DuckDB
  can spill to disk when a step passes its memory cap. Chosen.
- Track loads by month name only. A file that TLC publishes again would be skipped
  without notice. Rejected: the log keys on file name and SHA-256.

## Decision

### DuckDB settings

Every connection is opened by one function, `pipeline/db.py: connect()`. It sets:

- `memory_limit` to `2GB`, or lower if asked. It refuses anything higher.
- `threads` to 2.
- `temp_directory` to `data/tmp/`, so big steps spill to disk.
- `preserve_insertion_order` to `false`.

Tests use the same function with a temporary folder. No other code calls
`duckdb.connect`.

### Load log

`load_log` has one row per (file name, SHA-256) pair. Its DDL is
`sql/schema/01_load_log.sql`.

| Column | Meaning |
|---|---|
| `load_id` | `INTEGER` primary key |
| `file_name`, `sha256` | `NOT NULL`, `UNIQUE` together. The hash is of the whole file, in 1 MiB chunks |
| `source_month` | `YYYY-MM`, `NOT NULL` |
| `size_bytes`, `last_modified` | From the download (ADR 0001) |
| `status` | `downloaded`, `loaded` or `replaced` |
| `downloaded_at`, `loaded_at` | Timestamps |
| `rows_in_file` | Every row in the file |
| `rows_other_platform` | Rows with a license number other than `HV0003` or `HV0005`. Out of scope, not quarantined |
| `rows_kept`, `rows_quarantined` | Uber and Lyft rows that passed or failed the rules |
| `rows_fee_part_null` | Kept rows where a fee part was null and counted as zero |
| `fact_rows` | Rows written to `fact_trip_hourly` |
| `extra_columns` | Source columns the pipeline ignores, if any |
| `package_version` | Version of this package at load time |

Every loaded month must satisfy
`rows_kept + rows_quarantined + rows_other_platform = rows_in_file`. The load checks this
before it commits.

### Download (T05)

`download --month YYYY-MM` fetches one file to `data/raw/` as a `.part` file, hashes it,
and renames it when complete. It then writes or updates the `load_log` row with status
`downloaded`.

- Skip: if the file is on disk and its hash matches a `load_log` row, nothing is
  fetched.
- Disk: before fetching, it checks that free space is at least the file size from a
  HEAD request plus 20%. If not, it exits with code 4 and fetches nothing.
- `--backfill YYYY-MM` fetches again even if the file is present. The old file is
  renamed to `data/raw/replaced/<name>.<first 8 hash characters>`. Code never deletes
  a downloaded file.
- The committed record is `reports/load_log.json`: month, file name, hash, size,
  `Last-Modified` and status, one entry per log row. It holds no trip rows.

### Load (T06)

`load --month YYYY-MM` runs these steps for one month. Steps 1 to 4 write only
staging tables. Step 5 is one transaction, so a crash leaves the month as it was.

1. Skip check. Hash the local file. If `load_log` has a `loaded` row with the same file
   name and hash, print `skip YYYY-MM: already loaded` and exit 0. If the month is
   loaded with a different hash, exit with code 3 and say to use `--backfill`. It never
   reloads on its own.
2. Disk check. Free space must be at least three times the file size (staging copy,
   spill files and the new fact rows). Otherwise exit with code 4.
3. Schema check, from the Parquet footer with pyarrow, before any row is read. See the
   rules below. A schema failure exits with code 2 and writes nothing.
4. Stage. `CREATE OR REPLACE TABLE stg_trip AS SELECT ... FROM read_parquet(path,
   file_row_number = true)`, which casts each column to the target type and keeps the
   file row number as `src_row`. A missing `cbd_congestion_fee` becomes `NULL`. Then
   each Uber and Lyft row gets its first failing reason code, or none.
5. Write, in one transaction: insert the failed rows into `quarantine_trip`, insert the
   kept rows grouped to the fact grain, write the `load_log` counts with status
   `loaded`, check the row-count sum and the dimension keys, then commit. Drop the
   staging tables after.

`load --backfill YYYY-MM` does the same steps for a month that is already loaded. In
the step 5 transaction it first deletes that month's fact rows and quarantine rows. If
the hash is the same, it updates the same `load_log` row. If the hash is new, it marks
the old row `replaced` and adds a new one. The plan's check "running the load again
adds zero rows" holds for both.

`load --all` runs every downloaded month in order and stops at the first failure. Each
month is still its own transaction, so a re-run picks up where the last one stopped.

### Schema rules (whole file)

These fail the month. They are not row codes, because the plan's stop rule says a month
that does not cast to the target schema stops the study.

| Code | Rule |
|---|---|
| `SCHEMA_MISSING_COLUMN` | A target column is missing. `cbd_congestion_fee` may be missing only in months before 2025-01 |
| `SCHEMA_BAD_TYPE` | A column's type is not in its allowed set. Text: `string` or `large_string`. Zone IDs: `int32` or `int64`. `trip_time`: `int32` or `int64`. Times: `timestamp` in any unit. Money and miles: `double` or `float` |

The target schema is the 25 columns in ADR 0001, cast to `VARCHAR`, `INTEGER`, <!-- not-a-claim -->
`BIGINT`, `TIMESTAMP` and `DOUBLE`. That covers the three schemas ADR 0001 found.
Extra columns are ignored and listed in `extra_columns`.

### Row rules and reason codes

Rules run in this order. A row gets the code of the first rule it fails, so each row
has exactly one code and the counts add up. The rule list lives once, in
`pipeline/rules.py`, as (code, SQL condition, plain description). The load, the docs
and the tests all read that list.

| Order | Code | A row fails when |
|---|---|---|
| 1 | `NULL_REQUIRED` | `hvfhs_license_num`, `pickup_datetime`, `dropoff_datetime`, `PULocationID`, `DOLocationID`, `trip_miles`, `trip_time` or `base_passenger_fare` is null |
| 2 | `PICKUP_OUTSIDE_MONTH` | `pickup_datetime` is not in the file's month |
| 3 | `UNKNOWN_ZONE` | `PULocationID` or `DOLocationID` is not a `zone_key` in `dim_zone` |
| 4 | `NEGATIVE_FARE` | `base_passenger_fare`, `tolls`, `bcf`, `sales_tax`, `congestion_surcharge`, `airport_fee` or `cbd_congestion_fee` is below zero |
| 5 | `NEGATIVE_DISTANCE` | `trip_miles` is below zero |
| 6 | `IMPOSSIBLE_DURATION` | `trip_time` is zero or less, `dropoff_datetime` is not after `pickup_datetime`, or either length is over six hours (`21600` seconds) |
| 7 | `DUPLICATE_TRIP` | The row is an exact copy of an earlier row in the same file, and passed rules 1 to 6 |

Notes on the choices:

- Rows with a null license number get `NULL_REQUIRED`, because their platform is
  unknown. Rows from other platforms are counted in `rows_other_platform` and dropped.
- Null fee parts (`tolls`, `bcf`, `sales_tax`, `congestion_surcharge`, `airport_fee`,
  `cbd_congestion_fee`) are not quarantined. They count as zero in the rider price,
  and `rows_fee_part_null` counts them per month. Quarantining them could push a month
  past the plan's 2% quarantine stop rule for a column the main outcome does not use. <!-- not-a-claim -->
  `docs/whats_weak.md` must report this count.
- A zero fare is kept. It can be a promotion. Only fares below zero are impossible.
- The six-hour limit is wide on purpose. It removes clock errors, not long trips.
- Rule 2 makes each trip belong to one file. That stops the same trip from being
  loaded from two monthly files.
- There is no trip ID in the files, so a duplicate means two rows that are equal in
  every source column. Near copies (one field differs) are both kept. To find copies,
  the load hashes the source columns with `md5_number` (128 bits) over a fixed text <!-- not-a-claim -->
  form where nulls have their own marker. It then groups by that hash, which DuckDB can
  spill to disk, and keeps the row with the lowest `src_row` in each group.

### Quarantine table

`quarantine_trip`, DDL in `sql/schema/06_quarantine_trip.sql`:

- Key: `(load_id, src_row)`, primary key.
- `source_month`, and `reason_code`, which is `NOT NULL` with a `CHECK` that it is one
  of the seven codes above.
- `reason_detail`: the first column that failed, for codes 1, 3 and 4.
- All 25 source columns, as cast in staging, so a reviewer can see the bad values. <!-- not-a-claim -->

Quarantined rows stay in the gitignored warehouse. Only counts by month and reason
reach git, in `reports/load_summary.json` (T06).

### The check command (T07)

`check` runs every test below, prints one line per failure, and exits 1 if any failed.
It exits 0 when all pass and 2 for bad arguments. It never runs on a timer. The
pre-analysis checks in the plan are part of it.

Freshness:

- Every month in the config (2023-01 to 2025-03 by default, or `--through YYYY-MM`) has
  exactly one `loaded` row in `load_log`.
- The newest `date_key` in the fact table falls in the last expected month.
- Each raw file on disk still has the size and modified time recorded at load. If not,
  the warehouse is stale for that month. This test does not hash, so it stays fast.

Row counts:

- For each month, the row-count sum above holds.
- Fact `trip_count` summed per month equals `rows_kept`. Quarantine rows per month
  equal `rows_quarantined`.
- Every day in a loaded month has at least one fact row for each platform.
- Each month's `rows_kept` is between half and one and a half times the median over
  loaded months. A cut-off file would fail here.
- Each month's quarantine share is at or below `max_quarantine_share` in the plan's
  parameter block. The check reads the block itself, so the two cannot drift.

Integrity:

- No fact key is missing from its dimension table (zone, date, platform).
- The fact primary key has no repeats. DuckDB enforces this on insert, and the check
  confirms it.

Messages name the test, the month and both numbers, for example
`FAIL row_count 2024-11: fact trips 101 != rows_kept 100`.

### Module layout

| Path | What |
|---|---|
| `src/nyc_rideshare_congestion/config.py` | Paths, months, limits. No numbers in other modules |
| `src/nyc_rideshare_congestion/params.py` | Reads the JSON parameter block in `docs/eval_plan.md` |
| `src/nyc_rideshare_congestion/cli.py` | The command line, with `argparse` |
| `src/nyc_rideshare_congestion/sources/tlc.py` | HEAD, download, SHA-256 (T05). The only network code with `manifest.py` |
| `src/nyc_rideshare_congestion/pipeline/db.py` | `connect()` and running the DDL |
| `src/nyc_rideshare_congestion/pipeline/schema.py` | Target schema and the footer check |
| `src/nyc_rideshare_congestion/pipeline/rules.py` | The row rules and their order |
| `src/nyc_rideshare_congestion/pipeline/dims.py` | Loads `dim_zone`, `dim_date`, `dim_platform` |
| `src/nyc_rideshare_congestion/pipeline/load.py` | Stage, validate, quarantine, fact load, backfill |
| `src/nyc_rideshare_congestion/pipeline/checks.py` | Freshness, row counts, integrity |
| `src/nyc_rideshare_congestion/pipeline/transforms.py` | Runs `sql/transforms/` (T07) |
| `src/nyc_rideshare_congestion/pipeline/summary.py` | Writes `reports/load_log.json` and `reports/load_summary.json` |
| `src/nyc_rideshare_congestion/pipeline/synthetic.py` | Writes small fake months and a fake zone lookup for tests and the demo |
| `sql/schema/` | DDL (ADR 0003) |
| `sql/load/` | Static SQL for staging and the fact insert |
| `sql/transforms/` | Hourly demand, platform share, fare per mile by zone (T07) |

The template modules (`data.py`, `evaluate.py`, `metrics.py`, `models.py`, `plots.py`)
are replaced by later tasks. Any that are no longer used move to `docs/archive/`.

### Commands

The package gets one entry point, `nrc`, run as `uv run nrc <command>`.

| Command | What it does |
|---|---|
| `download --month YYYY-MM`, `--all`, `--backfill YYYY-MM` | Fetch files, hash them, log them |
| `init` | Create the warehouse from `sql/schema/`, seed `dim_platform`, fill `dim_date`, load `dim_zone` |
| `load --month YYYY-MM`, `--all`, `--backfill YYYY-MM` | Validate, quarantine, load |
| `check [--through YYYY-MM]` | Freshness, row counts, integrity. Exit 1 on any failure |
| `transform` | Build the T07 transform tables |
| `summary` | Write the two report files |
| `demo` | The demo below |

Exit codes: 0 done or skipped, 1 a check failed, 2 schema error or bad arguments, 3 a
month changed and needs `--backfill`, 4 not enough disk.

### What `make demo` runs

`make demo` runs `uv run nrc demo`. It runs offline and needs no keys.

1. Writes a fake zone lookup and two fake months into a temporary folder with
   `pipeline/synthetic.py`. The months are 2024-12 and 2025-01, so one has the fee <!-- not-a-claim -->
   column and one does not. They hold a few hundred made-up rows, with at least one
   row for each reason code, some exact copies and some other-platform rows.
2. Runs `init`, then `load --all`, and prints rows kept and quarantined per reason.
3. Runs `load --all` again and prints that both months were skipped and no rows were
   added.
4. Runs `check` and prints that it passed. Then runs it with `--through` one month
   later and prints the failure it reports, to show that it fails loudly.
5. Runs `transform` and prints the first rows of each table.

The demo writes nothing inside the repo. The fake data is random with a fixed seed and
is not shaped like real trips, so it says nothing about NYC.

### Tests

Every test builds its own data with `pipeline/synthetic.py` under pytest's `tmp_path`.
No real trips, no network (an autouse fixture blocks sockets), no large files. Parquet
fixtures are written at test time, not committed. The spec's four come first.

1. Malformed rows: a month with one row per row rule. Each lands in `quarantine_trip`
   with its code, and the row-count sum holds.
2. Missing column: a month without `DOLocationID` exits with code 2 and leaves
   `load_log`, the fact and the quarantine table unchanged.
3. Duplicate events: exact copies keep one row and quarantine the rest as
   `DUPLICATE_TRIP`. Near copies are both kept.
4. Re-run without duplicates: loading a month twice adds zero rows to any table, and
   the second run reports a skip.
5. Rule order: a row that fails two rules gets the first code.
6. The fee column: missing before 2025 loads with zero fee. Missing in 2025 is
   `SCHEMA_MISSING_COLUMN`.
7. Both source type sets (`string` with `int64` zones, and `large_string` with `int32`)
   load to the same target types.
8. Other platforms are counted in `rows_other_platform`, never quarantined or kept.
9. Null fee parts count as zero in the rider price and in `rows_fee_part_null`.
10. Backfill with the same file changes no counts. A changed file without `--backfill`
    exits with code 3. With it, the new rows replace the old ones and the old log row
    is `replaced`.
11. A failure inside the write step (patched to raise) leaves no partial rows.
12. `check` passes on a complete fake warehouse. It exits 1 with a clear message for a
    missing month, a count that does not add up, an orphan zone key, a day with no rows
    for a platform, and a stale raw file.
13. `connect()` sets the four settings and refuses a memory limit over 2 GB.
14. `in_congestion_zone` in `dim_zone` matches the plan's `treated_zones`.
15. The fact primary key rejects a repeated cell.
16. Download (T05): with a patched HTTP client, it hashes and logs the file, skips a
    file it has, renames the old file on backfill, and refuses when disk is short.
17. The demo runs end to end with sockets blocked.

## Consequences

- Re-running any command is safe. That is how a long download or load resumes.
- A republished TLC file is never loaded without notice. It needs an explicit
  `--backfill`, and the old log row stays as a record.
- Counting null fee parts as zero is a judgment call. Its size per month is in the load
  log, and the docs must report it.
- The checks depend on the plan's parameter block for the quarantine limit. Changing
  that limit after results exist is a deviation (BRIEF), not a config change.
- The design adds one runtime dependency, `duckdb`, which T05 or T06 adds with
  `uv add duckdb`.
