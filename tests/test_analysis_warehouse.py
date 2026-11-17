"""The analysis steps that read the warehouse: zones, panel, stop rules and the commands.

Each test builds a tiny warehouse under tmp_path with hand-made fact rows, so the
expected counts can be worked out by hand.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
import shapefile

from nyc_rideshare_congestion import cli, config, params
from nyc_rideshare_congestion.analysis import estimate, panel, run, stop_rules, zones
from nyc_rideshare_congestion.pipeline import db, dims

PLAN = params.load_params()
BOROUGHS = {4: "Manhattan", 12: "Manhattan", 24: "Manhattan", 43: "Manhattan", 11: "Brooklyn"}
BOROUGHS |= {7: "Queens", 3: "Bronx", 1: "EWR", 264: "Unknown"}
GROUPS = zones.ZoneGroups(
    treated=(4, 12),
    control_manhattan=(24,),
    control_brooklyn=(11,),
    buffer=(43,),
    island=(),
    excluded_pickup=(1, 264, 265),
    side_a=("Brooklyn", "Staten Island"),
    side_b=("Manhattan", "Bronx"),
)


def _fact(
    con: duckdb.DuckDBPyConnection,
    day: int,
    pu: int,
    do: int,
    n: int,
    platform: int = 1,
    fee: float = 0.0,
) -> None:
    con.execute(
        "INSERT INTO fact_trip_hourly VALUES (?, 8, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
        [day, platform, pu, do, n, 2.0 * n, 600 * n, 15.0 * n, 20.0 * n, fee, n if fee else 0],
    )


def _zone_rows(con: duckdb.DuckDBPyConnection, boroughs: dict[int, str]) -> None:
    con.executemany(
        "INSERT INTO dim_zone VALUES (?, ?, ?, 'Boro Zone', false)",
        [[z, b, f"zone {z}"] for z, b in sorted(boroughs.items())],
    )


@pytest.fixture
def con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    c = db.connect(tmp_path / "w.duckdb", tmp_path / "tmp", memory_limit_mb=500)
    db.run_schema(c)
    _zone_rows(c, BOROUGHS)
    dims.fill_dates(c, "2024-10-01", "2024-10-31")
    d1 = 20241001
    _fact(c, d1, 7, 4, 5, fee=7.5)  # Queens to treated: counted
    _fact(c, d1, 4, 24, 9)  # starts in a treated zone: left out
    _fact(c, d1, 1, 4, 9)  # Newark pickup: left out
    _fact(c, d1, 11, 24, 9)  # Brooklyn to Manhattan control: may drive through, left out
    _fact(c, d1, 7, 24, 3)  # Queens to Manhattan control: counted
    _fact(c, d1, 43, 11, 9)  # Manhattan buffer to Brooklyn control: left out
    _fact(c, d1, 3, 11, 9)  # Bronx to Brooklyn control: left out
    _fact(c, d1, 11, 4, 2)  # Brooklyn to treated: the rule is for control trips only
    _fact(c, d1, 7, 11, 4)  # Queens to Brooklyn: counted
    _fact(c, d1, 11, 11, 1, platform=2)  # within Brooklyn, Lyft: counted
    yield c
    c.close()


def test_panel_month_counts_only_the_trips_the_plan_counts(
    con: duckdb.DuckDBPyConnection,
) -> None:
    row = panel.build_month(con, GROUPS, "2024-10")
    assert row == {
        "month": "2024-10",
        "cells": 31 * 2 * 4,
        "panel_trips": 15,
        "fact_trips": 15,
        "equal": True,
    }
    again = panel.build_month(con, GROUPS, "2024-10")  # rebuilding gives the same table
    assert again["cells"] == row["cells"]
    cube = panel.load_cube(con, GROUPS, date(2024, 10, 1), date(2024, 10, 31))
    assert cube.values.shape == (31, 4, len(panel.MEASURES))
    first = cube.values[0, :, panel.M["trips"]].tolist()
    assert first == [7.0, 0.0, 5.0, 3.0]  # zones 4, 12, then control 11, 24
    assert cube.values[0, 2, panel.M["trips_lyft"]] == 1.0


def test_fee_shares_and_flags(con: duckdb.DuckDBPyConnection) -> None:
    panel.build_month(con, GROUPS, "2024-10")
    p = {
        "windows": {"after": ["2024-10-01", "2024-10-31"]},
        "flags": PLAN["flags"],
    }
    out = estimate.fee_shares(con, p)
    assert out["treated"]["trips"] == 7
    assert out["treated"]["fee_share"] == pytest.approx(5 / 7)
    assert out["treated"]["mean_fee_dollars"] == pytest.approx(7.5 / 7)
    assert out["control"]["fee_share"] == 0.0
    assert out["flags"]["treated_fee_share_low"]["raised"]
    assert not out["flags"]["control_fee_share_high"]["raised"]


def _windows(first: str, last: str) -> dict[str, Any]:
    names = ("before", "after", "placebo_before", "placebo_after")
    return {"windows": {n: [first, last] for n in names}, "excluded_dates": {"study": []}}


def test_stop_rules_on_a_tiny_warehouse(con: duckdb.DuckDBPyConnection) -> None:
    short = stop_rules.rule_platform_days(con, _windows("2024-10-01", "2024-10-02"), 1)
    assert short["result"] == "fail"
    assert "20241001 platform 2" not in short["short_platform_days"]
    assert "20241002 platform 1" in short["short_platform_days"]

    _fact(con, 20241002, 7, 264, 10)
    p = _windows("2024-10-01", "2024-10-01")
    p["windows"]["after"] = ["2024-10-02", "2024-10-02"]
    unknown = stop_rules.rule_unknown_dropoff(con, p, 0.01)
    assert unknown["before"] == 0.0 and unknown["after"] == 1.0
    assert unknown["result"] == "fail"

    con.execute(
        "INSERT INTO quarantine_trip (load_id, src_row, source_month, reason_code, DOLocationID)"
        " VALUES (1, 1, '2024-10', 'NEGATIVE_FARE', 4)"
    )
    gap = stop_rules.rule_quarantine_gap(con, GROUPS, ["2024-10"], 0.005)
    assert gap["by_month"][0]["control"] == 0.0
    assert gap["result"] == "fail"

    months = ["2024-10"]
    assert stop_rules.rule_months_loaded(con, months)["not_loaded_once"] == months
    assert stop_rules.rule_quarantine_share(con, months, 0.02)["result"] == "fail"
    assert stop_rules.window_months(_windows("2024-10-01", "2024-11-02")) == ["2024-10", "2024-11"]


def _square(x: int, y: int) -> list[list[list[int]]]:
    return [[[x, y], [x, y + 1], [x + 1, y + 1], [x + 1, y], [x, y]]]


def write_shapes(path: Path, cells: dict[int, tuple[str, int, int]]) -> Path:
    """A fake taxi_zones.zip: one unit square per zone at the given corner."""
    folder = path.parent / "shapes"
    folder.mkdir()
    with shapefile.Writer(str(folder / "taxi_zones"), shapeType=shapefile.POLYGON) as w:
        w.field("LocationID", "N", size=5)
        w.field("borough", "C", size=20)
        for zone, (borough, x, y) in cells.items():
            w.poly(_square(x, y))
            w.record(zone, borough)
    with zipfile.ZipFile(path, "w") as zf:
        for f in folder.iterdir():
            zf.write(f, f"taxi_zones/{f.name}")
    return path


def test_zone_lists_are_rebuilt_and_checked(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    cells = {4: ("Manhattan", 0, 0), 12: ("Manhattan", 0, 1), 43: ("Manhattan", 1, 0)}
    cells |= {24: ("Manhattan", 5, 5), 11: ("Brooklyn", 9, 9), 7: ("Queens", 20, 20)}
    shapes = write_shapes(tmp_path / "taxi_zones.zip", cells)
    info = zones.verify(con, GROUPS, shapes)
    assert info["buffer_rebuilt_from_shapes"] == [43]

    wrong = zones.ZoneGroups(**{**GROUPS.__dict__, "buffer": (24,)})
    with pytest.raises(zones.ZoneListError, match="buffer"):
        zones.verify(con, wrong, shapes)


def test_plan_zone_groups() -> None:
    g = zones.groups_from_params(PLAN)
    assert len(g.treated) == 38 and len(g.control) == len(set(g.control))
    assert not set(g.treated) & set(g.control)
    off = {**PLAN, "cross_zone_rule": {**PLAN["cross_zone_rule"]}}
    off["cross_zone_rule"]["drop_control_trip_if_ends_on_opposite_sides"] = False
    with pytest.raises(zones.ZoneListError, match="switched off"):
        zones.groups_from_params(off)
    twice = {**PLAN, "buffer_zones": [*PLAN["buffer_zones"], PLAN["treated_zones"][0]]}
    with pytest.raises(zones.ZoneListError, match="more than one group"):
        zones.groups_from_params(twice)


@pytest.fixture
def study(tmp_path: Path) -> Iterator[tuple[config.Paths, duckdb.DuckDBPyConnection]]:
    """A fake warehouse with the plan's zones and trips on every day of the study."""
    paths = config.Paths(tmp_path)
    c = db.connect(paths.db, paths.tmp, memory_limit_mb=500)
    db.run_schema(c)
    g = zones.groups_from_params(PLAN)
    boroughs = {z: "Manhattan" for z in (*g.treated, *g.control_manhattan, *g.buffer)}
    boroughs |= {z: "Brooklyn" for z in g.control_brooklyn} | {7: "Queens"}
    _zone_rows(c, boroughs)
    dims.fill_dates(c, "2023-01-01", "2026-09-30")
    c.execute(
        """
        INSERT INTO fact_trip_hourly
        SELECT d.date_key, 8, pl.platform_key, 7, z.zone,
               n, 2.0 * n, 600 * n, 15.0 * n, 20.0 * n,
               CASE WHEN d.full_date >= DATE '2025-01-05' AND z.treated THEN 1.5 * n ELSE 0 END,
               CASE WHEN d.full_date >= DATE '2025-01-05' AND z.treated THEN n ELSE 0 END,
               1
        FROM dim_date AS d
        CROSS JOIN dim_platform AS pl
        CROSS JOIN (
            SELECT unnest($t) AS zone, true AS treated
            UNION ALL SELECT unnest($c), false
        ) AS z,
        LATERAL (SELECT 20 + (d.date_key * 7 + z.zone * 13 + pl.platform_key) % 11 AS n)
        """,
        {"t": list(g.treated), "c": list(g.control)},
    )
    yield paths, c
    c.close()


def test_commands_run_end_to_end(
    study: tuple[config.Paths, duckdb.DuckDBPyConnection], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, con = study
    monkeypatch.setattr(zones, "verify", lambda *a: {"checked": "in another test"})
    assert run.main("panel", paths, con, None) == config.EXIT_OK
    summary = json.loads((paths.reports / "panel_summary.json").read_text(encoding="utf-8"))
    assert summary["months"] == 45 and summary["all_months_match_fact"]

    # The fake warehouse has no load log, so the stop rules fail and nothing is estimated.
    assert run.main("prechecks", paths, con, None) == config.EXIT_FAILED
    assert run.main("estimate", paths, con, None) == config.EXIT_FAILED
    stopped = json.loads((paths.reports / "estimate.json").read_text(encoding="utf-8"))
    assert stopped["verdict"]["table_rows"] == ["stop_rule_fails"]
    assert "months_loaded" in stopped["failed_rules"]
    assert run.main("extended", paths, con, None) == config.EXIT_FAILED
    stopped = json.loads((paths.reports / "extended.json").read_text(encoding="utf-8"))
    assert stopped["verdict"]["table_rows"] == ["stop_rule_fails"]
    stops = json.loads((paths.reports / "extended_stop_rules.json").read_text(encoding="utf-8"))
    assert stops["months"] == ["2023-01", "2026-09"]

    passing = {"result": "pass", "failed_rules": [], "pipeline_check_failures": []}
    monkeypatch.setattr(stop_rules, "run", lambda *a: passing)
    assert run.main("prechecks", paths, con, None) == config.EXIT_OK
    assert run.main("estimate", paths, con, None) == config.EXIT_OK
    out = json.loads((paths.reports / "estimate.json").read_text(encoding="utf-8"))
    assert out["fees_after_start"]["treated"]["fee_share"] == 1.0
    assert out["fees_after_start"]["control"]["fee_share"] == 0.0
    assert out["bootstrap"] == {"rounds": 2000, "seed": 20250105, "interval_level": 0.95}
    assert run.main("extended", paths, con, None) == config.EXIT_OK
    ext = json.loads((paths.reports / "extended.json").read_text(encoding="utf-8"))
    assert ext["label"] == "amendment" and ext["last_day_loaded"] == "2026-09-30"
    assert ext["year_over_year"]["days_kept"] == {"before": 362, "after": 361}
    assert ext["second_fee_year"]["after"] == ["2026-01-05", "2026-09-30"]
    assert ext["event_study_monthly"]["months"][0] == "2024-01"
    assert len(ext["zone_changes"]) == len(PLAN["treated_zones"]) + len(
        PLAN["control_zones_manhattan"]
    ) + len(PLAN["control_zones_brooklyn"])
    for name in run.FIGURES.values():
        assert (paths.root / "docs" / "img" / name).stat().st_size > 0


def test_extended_needs_the_panel_first(tmp_path: Path) -> None:
    paths = config.Paths(tmp_path)
    con = db.connect(paths.db, paths.tmp, memory_limit_mb=200)
    db.run_schema(con)
    con.execute((config.SQL_DIR / "analysis" / "01_panel_table.sql").read_text(encoding="utf-8"))
    with pytest.raises(FileNotFoundError, match="nrc panel"):
        run.run_extended(con, paths, PLAN)
    con.close()


def test_estimate_needs_the_stop_rules_first(tmp_path: Path) -> None:
    paths = config.Paths(tmp_path)
    con = db.connect(paths.db, paths.tmp, memory_limit_mb=200)
    with pytest.raises(FileNotFoundError, match="prechecks"):
        run.run_estimate(con, paths, PLAN)
    con.close()


def test_cli_builds_one_panel_month(
    study: tuple[config.Paths, duckdb.DuckDBPyConnection],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, con = study
    con.close()
    monkeypatch.setattr(zones, "verify", lambda *a: {})
    assert cli.main(["--root", str(paths.root), "panel", "--month", "2024-10"]) == 0
    assert "panel 2024-10: 7502 cells" in capsys.readouterr().out
    assert not (paths.reports / "panel_summary.json").exists()  # one month writes no summary
