"""The offline pipeline demo for `make demo` and `nrc demo` (ADR 0004).

It writes a fake zone lookup and two fake months into a temporary folder, then runs the
real `nrc` commands on them: init, load, load again, check, check one month too far,
and transform. It needs no downloads, no network and no keys, and writes nothing
inside the repo. The fake rows are random with a fixed seed and say nothing about NYC.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.pipeline import db, synthetic, transforms

MONTHS = ("2024-12", "2025-01")  # the first has no fee column, the second has one
NOT_LOADED = "2025-02"  # one month past the fake data, so `check` must fail
EXTRA_TRIPS = 1500  # random valid trips per month on top of one per day and platform
PREVIEW_ROWS = 3


def _rows(month: str) -> list[synthetic.Row]:
    """Valid trips for every day, one bad row per rule, two exact copies, one near copy
    and three other-platform rows."""
    rows = synthetic.full_month_rows(month, extra=EXTRA_TRIPS)
    rows += list(synthetic.bad_rows(month).values())
    rows += [dict(rows[0]), dict(rows[0])]  # exact copies: one kept, two quarantined
    rows.append({**rows[1], "tips": rows[1]["tips"] + 1.0})  # near copy: both kept
    rows += [
        synthetic.trip(month, 20_000 + k, hvfhs_license_num=synthetic.OTHER_LICENSE)
        for k in range(3)
    ]
    return rows


def write_fixtures(paths: config.Paths) -> None:
    """Write the fake lookup and months into paths.raw and log them like `nrc download`."""
    synthetic.write_zone_lookup(paths.raw / config.ZONE_LOOKUP_FILE)
    con = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    try:
        db.run_schema(con)
        for month in MONTHS:
            fee = month >= config.FEE_COLUMN_FIRST_MONTH
            synthetic.place_month(paths, con, month, _rows(month), fee=fee)
    finally:
        con.close()


def _step(title: str, argv: Sequence[str], root: Path) -> int:
    from nyc_rideshare_congestion import cli  # cli imports this module for `nrc demo`

    print(f"\n== {title}: nrc {' '.join(argv)}")
    return cli.main(["--root", str(root), *argv])


def _cell(value: object) -> str:
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def _preview(paths: config.Paths) -> None:
    con = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    try:
        for sql_file in sorted(transforms.TRANSFORM_SQL.glob("*.sql")):
            table = transforms.table_name(sql_file)
            print(f"\n{table}, first {PREVIEW_ROWS} rows:")
            rel = con.sql(f"SELECT * FROM {table} ORDER BY ALL LIMIT {PREVIEW_ROWS}")
            print("  " + " | ".join(rel.columns))
            for row in rel.fetchall():
                print("  " + " | ".join(_cell(v) for v in row))
    finally:
        con.close()


def run(root: Path) -> int:
    """Run the demo in an empty folder. Returns 0 if every step did what it should."""
    paths = config.Paths(root)
    write_fixtures(paths)
    first, last = MONTHS[0], MONTHS[-1]
    steps: list[tuple[str, list[str], int]] = [
        ("Create the warehouse", ["init"], config.EXIT_OK),
        ("Validate, quarantine and load", ["load", "--all"], config.EXIT_OK),
        ("Load again: both months skip, no rows added", ["load", "--all"], config.EXIT_OK),
        ("Check the loaded months", ["check", "--from", first, "--through", last], config.EXIT_OK),
        (
            "Check one month past the data: this should fail",
            ["check", "--from", first, "--through", NOT_LOADED],
            config.EXIT_FAILED,
        ),
        ("Build the transform tables", ["transform"], config.EXIT_OK),
    ]
    for title, argv, expected in steps:
        code = _step(title, argv, root)
        if code != expected:
            print(f"FAIL demo: `nrc {' '.join(argv)}` exited {code}, expected {expected}")
            return config.EXIT_FAILED
    _preview(paths)
    print("\ndemo finished: every step did what it should")
    return config.EXIT_OK


SAMPLE_NOTE = (
    "DEMO ON A SMALL SYNTHETIC SAMPLE: two made-up months with a fixed seed, not real "
    "trips.\nIt downloads nothing and finishes in seconds."
)
REAL_NOTE = (
    "The numbers in the README come from the real TLC files, loaded with "
    "`nrc download --all` and `nrc load --all`.\nThis demo's counts say nothing about NYC."
)


def main() -> int:
    print(SAMPLE_NOTE)
    print(REAL_NOTE)
    with tempfile.TemporaryDirectory(prefix="nrc-demo-", ignore_cleanup_errors=True) as tmp:
        code = run(Path(tmp))
    print(f"\n{SAMPLE_NOTE.splitlines()[0]}\n{REAL_NOTE.splitlines()[0]}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
