# 0007: SQL transforms, the check command and the index timing

Date: 2026-10-29. Status: accepted.

## Context

ADR 0004 names three transforms (hourly demand, platform share, fare per mile by zone)
and the tests the `check` command runs. ADR 0003 names the index to time:
`ix_fact_do_zone` on `fact_trip_hourly (do_zone_key)`. Building them raised questions
those ADRs leave open. The biggest one came from the real warehouse: on this machine,
`CREATE INDEX ix_fact_do_zone` on the whole fact table ran out of memory under the
DuckDB memory cap from the brief. DuckDB builds a new index in memory and cannot spill
it to disk, so the build fails before it finishes.

## Options

For the index timing, once the full-table build failed:

- Raise the memory cap for this one command. Rejected: the brief caps every
  connection, and the machine has little free memory.
- Report only the failure. Rejected: the task asks for a before and after timing.
- Record the failure, then time the same index on a fixed slice that fits: the newest
  three loaded months, copied into a scratch database in `data/tmp/`. Also time a copy
  of the slice sorted by drop-off zone. That is the closest real DuckDB equivalent of
  an index for this query, because DuckDB keeps a min and max per block of rows and
  skips blocks whose range cannot match. Chosen.

## Decision

| Question | Answer |
|---|---|
| Transform grain | Hourly demand: date, hour and pickup borough. Platform share: month, pickup borough and platform. Fare per mile: month, pickup zone and platform |
| Empty cells | Hourly demand and platform share fill missing cells with zero trips, so window averages and shares count quiet hours and absent platforms |
| Windows | Hourly demand: the same hour over the last seven calendar days (a `RANGE` window, so missing days do not shift it), the same hour a week earlier, share of the day and rank in the day. Platform share: change from the previous month. Fare per mile: a three-month rolling ratio by calendar month and rank in the borough |
| Fare per mile | Sum of base fares over sum of miles, so long trips weigh more. Zero miles gives a null ratio, not an error. It uses `base_fare_sum`, not the rider price, so tolls and fees do not enter it |
| Where transforms live | `sql/transforms/NN_*.sql`, one `CREATE OR REPLACE TABLE tf_*` per file, run in name order by `nrc transform` |
| Check range | `nrc check` takes `--from` and `--through` (both study months). `--from` after `--through` exits 2 |
| Raw file unchanged | Size must match exactly. Modified time may differ from the logged `Last-Modified` by up to `MTIME_TOLERANCE_S` in `config.py`, for file systems that round times |
| Months far from the median | `ROWS_KEPT_BAND` in `config.py`, half to one and a half times the median month, as ADR 0004 says |
| Repeated primary keys | Counted one month at a time, with the five key columns packed into one number, so the count fits in the memory cap |
| Index timing command | `nrc time-index` writes `reports/index_timing.json` with the machine, the DuckDB settings and a note that the numbers come from one machine |
| Timed query and zones | ADR 0003's query (daily trips for one drop-off zone) for three zones picked by rule: most, median and fewest fact rows. One untimed warm-up, then `TIMING_RUNS` runs; the median is reported |
| Full table | Timed without the index, then the build is tried. Running out of memory is recorded as the result, with the error's first line. If the build works on a larger machine, the after timing is recorded and the index stays |
| Slice | The newest `TIMING_SLICE_MONTHS` loaded months, copied to a scratch database in `data/tmp/`. Timed as copied, sorted by drop-off zone, and with the index. The scratch file is removed at the end, even after an error |
| Index used or not | Read from `EXPLAIN ANALYZE`. In the DuckDB version in `uv.lock`, the scan type is picked when the query runs, so plain `EXPLAIN` always shows a sequential scan |

## Consequences

- The warehouse has no `ix_fact_do_zone` on this machine. Nothing depends on it.
- The report shows what DuckDB documents: it uses an index only when a filter matches
  few rows (at most `index_scan_max_count`, or a small share of the table). In the
  slice, only the zone with the fewest rows got an index scan. The sorted copy was
  faster for all three zones. So for this query, sort order is the tool that helps on
  DuckDB, not an index.
- The timings are single-machine numbers from one run. They show the size of the
  effect on this laptop, not a benchmark.
- The transforms read every month, including the study months. They are pipeline
  outputs, not study results. Only `nrc transform` row counts are printed, and no
  transform output is committed before T09.
- `docs/whats_weak.md` (T11) should list the missing full-table index as a limit of
  the 2 GB setup.
