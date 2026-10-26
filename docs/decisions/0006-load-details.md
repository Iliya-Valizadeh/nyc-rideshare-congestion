# 0006: Load details that ADR 0004 leaves open

Date: 2026-10-26. Status: accepted.

## Context

ADR 0003 sets the tables and ADR 0004 sets the load steps, the row rules and the
quarantine table. Building the load (`pipeline/load.py`) raised a few questions that
neither ADR answers.

## Options

The main one is how to count `rows_fee_part_null` for months before 2025, where the
whole `cbd_congestion_fee` column is missing.

- Count the missing column as a null part on every row. Then every kept row in 2023
  and 2024 counts, and the number says nothing about real nulls. Rejected.
- Count only the fee parts the file has. A column that is missing from the whole file
  is a schema fact, already recorded by the schema check. Chosen.

## Decision

| Question | Answer |
|---|---|
| `rows_fee_part_null` when the fee column is missing from the file | Counts only the fee parts the file has (see above). The fee still counts as zero in the rider price |
| A file on disk whose hash has no current `load_log` row | The load refuses it with exit code 1 and says to run `nrc download`. This follows ADR 0005: the log only trusts files this code fetched. So `load --backfill` with a new file needs `download --backfill` first, which adds the new log row |
| Where the staging SQL lives | Built in Python from the target schema (`pipeline/schema.py`) and the rule list (`pipeline/rules.py`), so each list exists once. The two inserts that do not depend on those lists are static files in `sql/load/` |
| How `rows_other_platform` is counted | By its own scan of the file, not as the rest of the rows. Then the check that the three counts add up to the rows in the file is a real test, not a sum that holds by construction |
| How rule 3 finds known zones | It reads the zone keys from `dim_zone` at load time and writes them into the rule as a list |
| Warehouse size after each month (ADR 0003 asks for it) | A new `load_log.warehouse_bytes` column, filled after the month commits and the database is checkpointed. `01_load_log.sql` adds it with `ADD COLUMN IF NOT EXISTS`, so warehouses made before this ADR get it too. The number is the file size, which includes space that DuckDB freed after dropping the staging table and will reuse. It is an upper bound on what the tables need |
| A copy of a row that failed rules 1 to 6 | It fails the same rule, because the values are the same. Rule 7 only looks at rows that passed rules 1 to 6, as ADR 0004 says |
| Text form for the duplicate hash | Each of the target columns cast to text, nulls replaced by the control character 30, joined with the control character 31. The source text columns hold base numbers and Y/N flags, where these characters are not expected. A value that held one could make two different rows look equal; nothing checks for that | <!-- not-a-claim -->

## Consequences

- The fee-null count in the load log reflects real nulls in each file. Months before
  2025 report nulls in the five fee parts they have.
- A hand-copied file cannot be loaded, even if it is correct. That costs one download.
- `reports/load_summary.json` reports the warehouse size per month as an upper bound,
  not the exact size of the tables.
