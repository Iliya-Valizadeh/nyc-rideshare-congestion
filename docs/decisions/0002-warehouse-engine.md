# 0002: Warehouse engine

Date: 2026-10-21. Status: accepted.

## Context

The spec asks for a star schema in PostgreSQL, with keys and constraints. It also says
to use DuckDB for heavy Parquet work if PostgreSQL is slow, and to list only tools the
repo actually uses.

Facts that shape the choice:

- PostgreSQL is not installed. `where psql` finds nothing, and there is no
  `C:\Program Files\PostgreSQL` folder.
- The project rules allow no system software or service installs during the build. A
  PostgreSQL install would be a separate step I do by hand.
- The machine has 7 GB of RAM with little free. The pipeline may use at most 2 GB.
- The source is 13.05 GB of Parquet over 27 monthly files (ADR 0001).
- Tests, `make demo` and CI must run with no network and no running services.

## Options

- PostgreSQL. It is the engine most hiring managers know, and it enforces keys and
  constraints well. But it needs a system install and a running service. Loading many
  months of trips into row storage on this machine would be slow, and CI would need a
  service container. It cannot start until I install it by hand.
- DuckDB, one file at `data/warehouse/`. It is a Python package (`uv add duckdb`), so
  there is nothing to install system-wide. It reads Parquet directly and can cap its
  memory and spill to disk. It supports `PRIMARY KEY`, `FOREIGN KEY`, `UNIQUE`,
  `NOT NULL` and `CHECK` constraints, CTEs, window functions and `CREATE INDEX`. Tests
  can use an in-memory database. The cost: the repo shows DuckDB SQL, not PostgreSQL.
- Both: DuckDB for heavy work and PostgreSQL for the star schema. This doubles the
  setup, and the PostgreSQL half still waits for an install. Rejected.

## Decision

Use DuckDB for the whole warehouse. Keep the same star schema the spec describes, with
primary keys, foreign keys, `NOT NULL` and `CHECK` constraints declared in the DDL under
`sql/`. Every connection uses the settings from the project plan: `memory_limit` of 2GB or
less, `threads` 2, `temp_directory` set to `data/tmp/`, and
`preserve_insertion_order=false`.

PostgreSQL is not the better choice here, so no manual install is needed. The
reason in one line: DuckDB fits a 2 GB memory budget and runs in tests and CI with no
service, which PostgreSQL cannot do on this machine.

## Consequences

- The README and docs say DuckDB. They do not mention PostgreSQL as a tool used.
- This differs from the spec's wording. The spec allows DuckDB where PostgreSQL is
  slow; this ADR is the record of why.
- Write SQL in a portable style (standard CTEs and window functions, no DuckDB-only
  syntax where a standard form exists). A later PostgreSQL port then stays possible.
- DuckDB builds an in-memory index for each primary key and unique constraint. On a
  table with one row per trip, that index alone could pass the 2 GB limit. T04 must
  size this before it picks between `fact_trip` and `fact_trip_hourly`.
- DuckDB mostly speeds up scans with its automatic min-max indexes, and `CREATE INDEX`
  mostly helps point lookups. The spec's before-and-after index timing (T04 and later)
  must pick a query where an index can matter, and report the result even if it is
  small.
