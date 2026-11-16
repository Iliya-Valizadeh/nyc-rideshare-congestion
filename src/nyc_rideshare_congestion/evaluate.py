"""Headline numbers for `make eval`: reports/metrics.json, built from the analysis reports.

The analysis runs on the full warehouse: `nrc panel`, `nrc prechecks`, then
`nrc estimate`. Those commands write reports/stop_rules.json, pretrend.json,
placebo.json and estimate.json. This step reads them and collects the numbers the
README quotes. It needs no warehouse, so a test can check that the committed
metrics.json matches the committed reports.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

METRICS_FILE = "metrics.json"
PRIMARY = "trips_per_day_both_platforms"
DECIMALS = 6


def _read(reports_dir: Path, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((reports_dir / name).read_text(encoding="utf-8"))
    return data


def _interval(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "effect": row["effect"],
        "interval_zone": row["interval_zone"],
        "interval_week": row.get("interval_week"),
        "interval_leaves_out_zero": row["interval_leaves_out_zero"],
    }


def build_metrics(reports_dir: Path) -> dict[str, Any]:
    """Collect the headline from the analysis reports. A failed stop rule gives no numbers."""
    stops = _read(reports_dir, "stop_rules.json")
    est = _read(reports_dir, "estimate.json")
    out: dict[str, Any] = {
        "about": (
            "Part 2: did congestion pricing change Uber and Lyft trips into the zone? "
            "Built by `make eval` from the reports that `nrc prechecks` and `nrc estimate` write."
        ),
        "primary_outcome": PRIMARY,
        "comparison": "drop-offs in other Manhattan zones and all Brooklyn zones",
        "stop_rules": stops["result"],
        "verdict": est["verdict"],
    }
    if stops["result"] != "pass" or "main" not in est:
        out["failed_rules"] = stops.get("failed_rules", [])
        return out

    pre = _read(reports_dir, "pretrend.json")
    plc = _read(reports_dir, "placebo.json")
    panel = _read(reports_dir, "panel_summary.json")
    main = est["main"]["outcomes"][PRIMARY]
    fees = est["fees_after_start"]
    out |= {
        "zones": {
            k: panel["zones"][k] for k in ("treated", "control_manhattan", "control_brooklyn")
        },
        "days_kept": est["main"]["days_kept"],
        "main": {
            **_interval(main),
            "trips_per_day": {
                k: main[k]
                for k in ("treated_before", "treated_after", "control_before", "control_after")
            },
        },
        "pretrend_check": {
            "result": pre["outcomes"][PRIMARY]["result"],
            "drift_log_points": pre["outcomes"][PRIMARY]["drift"],
            "drift_interval_zone": pre["outcomes"][PRIMARY]["drift_interval_zone"],
            "drift_interval_week": pre["outcomes"][PRIMARY]["drift_interval_week"],
            "line": pre["outcomes"][PRIMARY]["size_line"],
        },
        "placebo_check": {
            "result": plc["main"]["outcomes"][PRIMARY]["result"],
            **_interval(plc["main"]["outcomes"][PRIMARY]),
            "line": plc["main"]["outcomes"][PRIMARY]["size_line"],
        },
        "triple_difference": _interval(est["triple_difference"][PRIMARY])
        | {"as_percent": est["triple_difference"][PRIMARY]["as_percent"]},
        "drift_adjusted": _interval(est["drift_adjusted"][PRIMARY]),
        "holidays_kept": _interval(est["holidays_kept"]["outcomes"][PRIMARY]),
        "fees_after_start": {
            "treated_fee_share": fees["treated"]["fee_share"],
            "treated_mean_fee_dollars": fees["treated"]["mean_fee_dollars"],
            "control_fee_share": fees["control"]["fee_share"],
            "flags": {k: v["raised"] for k, v in fees["flags"].items()},
        },
        "secondary": {
            name: {
                "unit": row["unit"],
                **_interval(row),
                "pretrend_check": pre["outcomes"][name]["result"],
                "placebo_check": plc["main"]["outcomes"][name]["result"],
            }
            for name, row in est["main"]["outcomes"].items()
            if name != PRIMARY
        },
        "bootstrap": est["bootstrap"],
    }
    return out


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, DECIMALS)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def write_metrics(reports_dir: Path) -> dict[str, Any]:
    metrics = _round(build_metrics(reports_dir))
    text = json.dumps(metrics, indent=2) + "\n"
    # Always LF, so a run on Windows writes the same bytes as a run on Linux.
    (reports_dir / METRICS_FILE).write_text(text, encoding="utf-8", newline="\n")
    return dict(metrics)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    args = parser.parse_args(argv)
    metrics = write_metrics(args.reports_dir)
    print(f"Wrote {args.reports_dir / METRICS_FILE}: {metrics['verdict']['table_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
