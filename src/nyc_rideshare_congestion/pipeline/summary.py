"""Write the committed record of what was downloaded and loaded.

`reports/load_log.json` lists each load_log row (month, file name, hash, size,
Last-Modified, status) and the hashed reference files. `reports/load_summary.json` has
rows loaded and quarantined per month and reason. Neither holds trip rows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.pipeline import rules
from nyc_rideshare_congestion.sources.tlc import sha256_file


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.strftime("%Y-%m-%dT%H:%M:%SZ")


def load_log_entries(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """One entry per load_log row, in month order."""
    rows = con.execute(
        "SELECT source_month, file_name, sha256, size_bytes, last_modified, status "
        "FROM load_log ORDER BY source_month, load_id"
    ).fetchall()
    return [
        {
            "month": month,
            "file_name": name,
            "sha256": sha,
            "size_bytes": int(size),
            "last_modified": _iso(modified),
            "status": status,
        }
        for month, name, sha, size, modified, status in rows
    ]


def reference_entries(paths: config.Paths) -> list[dict[str, Any]]:
    """Hash, size and modified time of each reference file that is on disk."""
    out = []
    for name, url in config.REFERENCE_FILES.items():
        path = paths.raw / name
        if not path.exists():
            continue
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, UTC).replace(tzinfo=None)
        out.append(
            {
                "file_name": name,
                "url": url,
                "sha256": sha256_file(path),
                "size_bytes": stat.st_size,
                "last_modified": _iso(modified),
            }
        )
    return out


def write_load_log(con: duckdb.DuckDBPyConnection, paths: config.Paths) -> Path:
    """Write reports/load_log.json and return its path."""
    entries = load_log_entries(con)
    have = {e["month"] for e in entries if e["status"] != "replaced"}
    record = {
        "months_expected": config.study_months(),
        "months_missing": [m for m in config.study_months() if m not in have],
        "load_log": entries,
        "reference_files": reference_entries(paths),
    }
    out = paths.reports / "load_log.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return out


_COUNTS = (
    "rows_in_file",
    "rows_other_platform",
    "rows_kept",
    "rows_quarantined",
    "rows_fee_part_null",
    "fact_rows",
)


def load_summary(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Rows loaded and quarantined per loaded month and reason, from the warehouse."""
    reasons: dict[str, dict[str, int]] = {}
    for month, code, n in con.execute(
        "SELECT source_month, reason_code, count(*) FROM quarantine_trip GROUP BY 1, 2"
    ).fetchall():
        reasons.setdefault(str(month), {})[str(code)] = int(n)
    rows = con.execute(
        f"SELECT source_month, file_name, sha256, {', '.join(_COUNTS)}, warehouse_bytes "
        "FROM load_log WHERE status = 'loaded' ORDER BY source_month"
    ).fetchall()
    months = []
    totals = dict.fromkeys(_COUNTS, 0)
    by_reason_total = dict.fromkeys(rules.CODES, 0)
    for month, name, sha, *counts, wh_bytes in rows:
        entry: dict[str, Any] = {"month": month, "file_name": name, "sha256": sha}
        entry.update({k: int(v) for k, v in zip(_COUNTS, counts, strict=True)})
        in_scope = entry["rows_kept"] + entry["rows_quarantined"]
        entry["quarantine_share"] = (
            round(entry["rows_quarantined"] / in_scope, 6) if in_scope else 0.0
        )
        entry["quarantined_by_reason"] = {c: reasons.get(month, {}).get(c, 0) for c in rules.CODES}
        entry["warehouse_bytes_after_load"] = None if wh_bytes is None else int(wh_bytes)
        months.append(entry)
        for k in _COUNTS:
            totals[k] += entry[k]
        for c in rules.CODES:
            by_reason_total[c] += entry["quarantined_by_reason"][c]
    in_scope = totals["rows_kept"] + totals["rows_quarantined"]
    loaded = [m["month"] for m in months]
    return {
        "command": "uv run nrc summary",
        "months_expected": config.study_months(),
        "months_loaded": len(loaded),
        "months_not_loaded": [m for m in config.study_months() if m not in loaded],
        "reason_codes": [
            {"order": r.order, "code": r.code, "description": r.description} for r in rules.RULES
        ],
        "totals": {
            **totals,
            "quarantine_share": round(totals["rows_quarantined"] / in_scope, 6)
            if in_scope
            else 0.0,
            "quarantined_by_reason": by_reason_total,
        },
        "months": months,
    }


def write_load_summary(con: duckdb.DuckDBPyConnection, paths: config.Paths) -> Path:
    """Write reports/load_summary.json and return its path. It holds counts only."""
    out = paths.reports / "load_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(load_summary(con), indent=2) + "\n", encoding="utf-8")
    return out
