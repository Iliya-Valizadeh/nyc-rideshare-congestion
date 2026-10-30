# data

This folder holds the data the pipeline writes on your machine. None of it is in git.

- `raw/`: the TLC monthly Parquet files and the taxi zone lookup, written by
  `nrc download`.
- `warehouse/`: the DuckDB file `warehouse.duckdb`, written by `nrc init` and
  `nrc load`.
- `tmp/`: disk space DuckDB uses when a step needs more than its memory cap.

Where the files come from and their terms of use are in [DATASHEET.md](../DATASHEET.md).
