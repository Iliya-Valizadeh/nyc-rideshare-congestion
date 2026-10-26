"""The `nrc` command line. Later tasks add init, load, check, transform and demo."""

from __future__ import annotations

import argparse
import re
from collections.abc import Sequence
from pathlib import Path

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.pipeline import db, summary
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

    sub.add_parser("summary", help="write reports/load_log.json")
    return parser


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
        if args.command == "summary":
            print(f"wrote {summary.write_load_log(con, paths)}")
            return config.EXIT_OK
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
