"""`nrc panel`, `nrc prechecks`, `nrc estimate` and `nrc extended`: the Part 2 analysis.

`panel` writes reports/panel_summary.json. `prechecks` writes reports/stop_rules.json,
reports/pretrend.json, reports/placebo.json and three charts in docs/img/. `estimate`
writes reports/estimate.json and the headline chart. It refuses to run unless the
stop rules in reports/stop_rules.json passed. `extended` is the longer window from
ADR 0009: it writes reports/extended_stop_rules.json, then reports/extended.json and
the monthly chart if those rules pass.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb

from nyc_rideshare_congestion import config

from . import estimate, extended, figures, prechecks, stop_rules, zones
from .bootstrap import zone_weights
from .calendar import parse_dates
from .panel import Cube, build_month, load_cube

SHAPES_FILE = "taxi_zones.zip"
IMG_DIR = config.REPO_ROOT / "docs" / "img"
FIGURES = {
    "trips": "pretrend_trips_indexed.png",
    "gap": "pretrend_log_ratio.png",
    "placebo": "placebo_weekly.png",
    "headline": "event_study_weekly.png",
    "extended": "event_study_monthly.png",
}


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def write_json(data: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_round(data), indent=2) + "\n", encoding="utf-8")
    return path


def build_panel(
    con: duckdb.DuckDBPyConnection, paths: config.Paths, p: dict[str, Any], months: list[str]
) -> dict[str, Any]:
    """Check the zone lists, then build each month of the panel and reconcile it."""
    groups = zones.groups_from_params(p)
    zone_info = zones.verify(con, groups, paths.raw / SHAPES_FILE)
    rows = [build_month(con, groups, m) for m in months]
    trips = con.execute(
        "SELECT zone_group, sum(trips) FROM panel_zone_day GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return {
        "unit": "drop-off zone x day x platform",
        "zones": zone_info,
        "months": len(rows),
        "cells": sum(r["cells"] for r in rows),
        "trips_by_group": {str(g): int(n) for g, n in trips},
        "all_months_match_fact": all(r["equal"] for r in rows),
        "by_month": rows,
    }


def load_study_cube(con: duckdb.DuckDBPyConnection, p: dict[str, Any]) -> Cube:
    groups = zones.groups_from_params(p)
    first = min(date.fromisoformat(w[0] + "-01") for w in [p["windows"]["pretrend_months"]])
    last = date.fromisoformat(p["windows"]["after"][1])
    return load_cube(con, groups, first, last)


def run_prechecks(
    con: duckdb.DuckDBPyConnection,
    paths: config.Paths,
    p: dict[str, Any],
    img_dir: Path = IMG_DIR,
) -> dict[str, Any]:
    """Stop rules first. Only if they pass: the pre-trend check and the placebo check."""
    groups = zones.groups_from_params(p)
    stops = stop_rules.run(con, p, groups, paths)
    write_json(stops, paths.reports / "stop_rules.json")
    if stops["result"] != "pass":
        return {"stop_rules": stops["result"]}
    cube = load_study_cube(con, p)
    b = p["bootstrap"]
    weights = zone_weights(
        len(groups.treated), len(groups.control), int(b["rounds"]), int(b["seed"])
    )
    pre = prechecks.pretrend(cube, p, weights)
    pre["bootstrap"] = {"rounds": int(b["rounds"]), "seed": int(b["seed"])}
    write_json(pre, paths.reports / "pretrend.json")

    plc = prechecks.placebo(cube, p, weights)
    names = ("placebo_before", "placebo_after")
    excluded = parse_dates(p["excluded_dates"]["placebo"])
    plc["weekly"] = prechecks.weekly_gap(cube, p, weights, names, excluded)
    plc["bootstrap"] = pre["bootstrap"]
    write_json(plc, paths.reports / "placebo.json")

    figures.plot_trips_indexed(pre, img_dir / FIGURES["trips"])
    figures.plot_log_gap(pre, img_dir / FIGURES["gap"])
    figures.plot_weekly_gap(
        plc["weekly"],
        date.fromisoformat(p["windows"]["placebo_after"][0]),
        "One year earlier, no fee: weekly log ratio, fee zone over control zones",
        img_dir / FIGURES["placebo"],
    )
    primary = "trips_per_day_both_platforms"
    return {
        "stop_rules": stops["result"],
        "pretrend": {k: v["result"] for k, v in pre["outcomes"].items()},
        "placebo": {k: v["result"] for k, v in plc["main"]["outcomes"].items()},
        "primary_pretrend_drift": pre["outcomes"][primary]["drift"],
        "primary_placebo_effect": plc["main"]["outcomes"][primary]["effect"],
    }


def _weights(p: dict[str, Any]) -> Any:
    groups = zones.groups_from_params(p)
    b = p["bootstrap"]
    return zone_weights(len(groups.treated), len(groups.control), int(b["rounds"]), int(b["seed"]))


def run_estimate(
    con: duckdb.DuckDBPyConnection,
    paths: config.Paths,
    p: dict[str, Any],
    img_dir: Path = IMG_DIR,
) -> dict[str, Any]:
    """The main estimate, if and only if the stop rules passed."""
    stops_file = paths.reports / "stop_rules.json"
    if not stops_file.exists():
        raise FileNotFoundError(f"{stops_file} is missing; run `nrc prechecks` first")
    stops = json.loads(stops_file.read_text(encoding="utf-8"))
    if stops["result"] != "pass":
        out = estimate.stopped(stops)
        write_json(out, paths.reports / "estimate.json")
        return out
    cube = load_study_cube(con, p)
    weights = _weights(p)
    slopes: dict[str, Any] = {}
    pre = prechecks.pretrend(cube, p, weights, slopes)
    plc = prechecks.placebo(cube, p, weights)
    fees = estimate.fee_shares(con, p)
    out = estimate.run_estimate(cube, p, weights, pre, slopes, plc, fees)
    b = p["bootstrap"]
    out["bootstrap"] = {
        "rounds": int(b["rounds"]),
        "seed": int(b["seed"]),
        "interval_level": float(b["interval_level"]),
    }
    write_json(out, paths.reports / "estimate.json")
    figures.plot_event_study(out["event_study"], img_dir / FIGURES["headline"])
    return out


def run_extended(
    con: duckdb.DuckDBPyConnection,
    paths: config.Paths,
    p: dict[str, Any],
    img_dir: Path = IMG_DIR,
) -> dict[str, Any]:
    """The longer window (ADR 0009): its stop rules first, then the estimate."""
    groups = zones.groups_from_params(p)
    loaded = [
        f"{int(k) // 100:04d}-{int(k) % 100:02d}"
        for (k,) in con.execute(
            "SELECT DISTINCT date_key // 100 FROM panel_zone_day ORDER BY 1"
        ).fetchall()
    ]
    if not loaded:
        raise FileNotFoundError("the panel is empty; run `nrc panel` first")
    months = config.months(config.FIRST_MONTH, loaded[-1])
    stops = stop_rules.run(con, extended.stop_rule_params(p), groups, paths, months)
    stops["months"] = [months[0], months[-1]]
    write_json(stops, paths.reports / "extended_stop_rules.json")
    if stops["result"] != "pass":
        out = estimate.stopped(stops)
        out["adr"] = "docs/decisions/0009-deviation-longer-window.md"
        write_json(out, paths.reports / "extended.json")
        return out
    first = date.fromisoformat(months[0] + "-01")
    last = prechecks.month_days(months[-1])[-1]
    cube = load_cube(con, groups, first, last)
    out = extended.run_extended(cube, p, _weights(p))
    b = p["bootstrap"]
    out["bootstrap"] = {
        "rounds": int(b["rounds"]),
        "seed": int(b["seed"]),
        "interval_level": float(b["interval_level"]),
        "week_blocks": "seven-day blocks, same block numbers in every window",
    }
    write_json(out, paths.reports / "extended.json")
    figures.plot_event_study_monthly(out["event_study_monthly"], img_dir / FIGURES["extended"])
    return out


def main(
    command: str, paths: config.Paths, con: duckdb.DuckDBPyConnection, months: list[str] | None
) -> int:
    """Run one analysis command and print a short result. Exit 1 if a check stops it."""
    from nyc_rideshare_congestion import params

    p = params.load_params()
    img_dir = paths.root / "docs" / "img"
    if command == "panel":
        todo = months or config.study_months()
        summary = build_panel(con, paths, p, todo)
        for row in summary["by_month"]:
            print(f"panel {row['month']}: {row['cells']} cells, {row['panel_trips']} trips")
        if months is None:
            print(f"wrote {write_json(summary, paths.reports / 'panel_summary.json')}")
        return config.EXIT_OK
    if command == "prechecks":
        result = run_prechecks(con, paths, p, img_dir)
        print(json.dumps(_round(result), indent=2))
        return config.EXIT_OK if result["stop_rules"] == "pass" else config.EXIT_FAILED
    if command == "extended":
        out = run_extended(con, paths, p, img_dir)
    else:
        out = run_estimate(con, paths, p, img_dir)
    print(json.dumps(_round(out["verdict"]), indent=2))
    return config.EXIT_FAILED if "failed_rules" in out else config.EXIT_OK
