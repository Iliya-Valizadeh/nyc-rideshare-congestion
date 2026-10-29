"""The `nrc` command line (ADR 0004 and 0007). The demo command comes in a later task."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

import duckdb

from nyc_rideshare_congestion import config, params
from nyc_rideshare_congestion.pipeline import (
    checks,
    db,
    dims,
    index_timing,
    load,
    schema,
    summary,
    transforms,
)
from nyc_rideshare_congestion.sources import tlc

_MONTH = re.compile(r"\d{4}-\d{2}")


def month_arg(value: str) -> str:
    """Accept YYYY-MM inside the study's months, or fail with exit code 2."""
    if not _MONTH.fullmatch(value) or value not in config.study_months():
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a study month ({config.FIRST_MONTH} to {config.LAST_MONTH})"
        )
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nrc", description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help="repo folder (default: here)")
    sub = parser.add_subparsers(dest="command", required=True)

    down = sub.add_parser("download", help="fetch TLC files, hash them and log them")
    which = down.add_mutually_exclusive_group(required=True)
    which.add_argument("--month", type=month_arg, help="one month, skipped if already logged")
    which.add_argument("--backfill", type=month_arg, help="fetch one month again")
    which.add_argument("--reference", action="store_true", help="zone lookup and shapes")
    which.add_argument("--all", action="store_true", help="reference files and every month")

    sub.add_parser("init", help="create the warehouse tables and fill the dimensions")

    ld = sub.add_parser("load", help="validate, quarantine and load downloaded months")
    which = ld.add_mutually_exclusive_group(required=True)
    which.add_argument("--month", type=month_arg, help="one month, skipped if already loaded")
    which.add_argument("--backfill", type=month_arg, help="load one month again")
    which.add_argument("--all", action="store_true", help="every downloaded month in order")

    sub.add_parser("summary", help="write reports/load_log.json and reports/load_summary.json")

    chk = sub.add_parser("check", help="freshness, row counts and integrity; exit 1 on failure")
    chk.add_argument(
        "--from", dest="first", type=month_arg, default=config.FIRST_MONTH, help="first month"
    )
    chk.add_argument("--through", type=month_arg, default=config.LAST_MONTH, help="last month")

    sub.add_parser("transform", help="build the tf_* tables from sql/transforms/")
    sub.add_parser("time-index", help="time the drop-off zone query before and after its index")
    return parser


def run_check(args: argparse.Namespace, paths: config.Paths, con: duckdb.DuckDBPyConnection) -> int:
    if args.first > args.through:
        print(f"FAIL check: --from {args.first} is after --through {args.through}")
        return config.EXIT_BAD_ARGS
    expected = config.months(args.first, args.through)
    stop_rules = params.load_params()["stop_rules"]
    failures = checks.run_checks(con, paths, expected, stop_rules)
    for failure in failures:
        print(failure.line())
    if failures:
        print(f"check failed: {len(failures)} problems in {args.first} to {args.through}")
        return config.EXIT_FAILED
    print(f"check passed: {len(expected)} months, {args.first} to {args.through}")
    return config.EXIT_OK


def run_transform(con: duckdb.DuckDBPyConnection) -> int:
    try:
        counts = transforms.run_transforms(con)
    except transforms.TransformError as err:
        print(f"FAIL transform: {err}")
        return config.EXIT_FAILED
    for table, rows in counts.items():
        print(f"transform {table}: {rows} rows")
    return config.EXIT_OK


def run_time_index(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> int:
    try:
        result = index_timing.run_timing(con, paths.tmp / config.TIMING_SCRATCH_DB)
    except ValueError as err:
        print(f"FAIL time-index: {err}")
        return config.EXIT_FAILED
    full, part = result["full_table"], result["slice"]
    print(f"full table, {full['fact_rows']} rows: index {full['index_build']['status']}")
    for zone in full["zones"]:
        after = zone["after"]["median_ms"] if zone["after"] else "-"
        print(
            f"  zone {zone['do_zone_key']} ({zone['label']}): "
            f"{zone['before']['median_ms']} ms -> {after} ms"
        )
    months = f"{part['months'][0]} to {part['months'][-1]}"
    print(f"slice {months}, {part['fact_rows']} rows: index {part['index_build']['status']}")
    for zone in part["zones"]:
        after = zone["after_index"]
        print(
            f"  zone {zone['do_zone_key']} ({zone['label']}): {zone['before']['median_ms']} ms, "
            f"sorted {zone['sorted_by_do_zone']['median_ms']} ms, "
            f"index {after['median_ms'] if after else '-'} ms, "
            f"index used: {after['uses_index'] if after else False}"
        )
    print(f"wrote {index_timing.write_report(result, paths.reports)}")
    return config.EXIT_OK


def run_init(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> int:
    lookup = paths.raw / config.ZONE_LOOKUP_FILE
    if not lookup.exists():
        print(f"FAIL init: {lookup} is missing; run `nrc download --reference` first")
        return config.EXIT_FAILED
    try:
        counts = dims.init_warehouse(con, lookup)
    except dims.ZoneChangeError as err:
        print(f"FAIL init: {err}")
        return config.EXIT_FAILED
    print("init: " + ", ".join(f"{k} {v} rows" for k, v in counts.items()))
    return config.EXIT_OK


def run_load(
    args: argparse.Namespace,
    paths: config.Paths,
    con: duckdb.DuckDBPyConnection,
    free_fn: tlc.DiskFreeFn,
) -> int:
    if args.all:
        months = load.loadable_months(paths, con)
        if not months:
            print("FAIL load: no downloaded months; run `nrc download` first")
            return config.EXIT_FAILED
    else:
        months = [args.month or args.backfill]
    for month in months:
        try:
            result = load.load_month(
                month, paths, con, backfill=bool(args.backfill), free_fn=free_fn
            )
        except schema.SchemaError as err:
            print(f"FAIL schema {month}: {err}")
            return config.EXIT_SCHEMA
        except load.NeedsBackfillError as err:
            print(f"FAIL load {month}: {err}")
            return config.EXIT_NEEDS_BACKFILL
        except load.LoadDiskError as err:
            print(f"FAIL disk {month}: {err}")
            return config.EXIT_DISK
        except load.LoadError as err:
            print(f"FAIL load {month}: {err}")
            return config.EXIT_FAILED
        print(result.line(), flush=True)
    return config.EXIT_OK


def _print(result: tlc.Result) -> None:
    print(f"{result.action} {result.name}: {result.size_bytes} bytes, sha256 {result.sha256}")


def main(
    argv: Sequence[str] | None = None,
    http: tlc.Http | None = None,
    free_fn: tlc.DiskFreeFn = tlc.disk_free,
) -> int:
    args = build_parser().parse_args(argv)
    paths = config.Paths(args.root or Path.cwd())
    con = db.connect(paths.db, paths.tmp)
    try:
        db.run_schema(con)
        if args.command == "init":
            return run_init(paths, con)
        if args.command == "load":
            return run_load(args, paths, con, free_fn)
        if args.command == "summary":
            print(f"wrote {summary.write_load_log(con, paths)}")
            print(f"wrote {summary.write_load_summary(con, paths)}")
            return config.EXIT_OK
        if args.command == "check":
            return run_check(args, paths, con)
        if args.command == "transform":
            return run_transform(con)
        if args.command == "time-index":
            return run_time_index(paths, con)
        client = http or tlc.UrllibHttp()
        try:
            if args.reference or args.all:
                for result in tlc.download_reference(paths, client, free_fn=free_fn):
                    _print(result)
            if args.month or args.backfill:
                month = args.month or args.backfill
                _print(
                    tlc.download_month(
                        month, paths, con, client, backfill=bool(args.backfill), free_fn=free_fn
                    )
                )
            if args.all:
                for month in config.download_order():
                    _print(tlc.download_month(month, paths, con, client, free_fn=free_fn))
        except tlc.DiskSpaceError as err:
            print(f"FAIL disk: {err}")
            return config.EXIT_DISK
        except (tlc.DownloadError, OSError) as err:
            print(f"FAIL download: {err}")
            return config.EXIT_FAILED
        return config.EXIT_OK
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
