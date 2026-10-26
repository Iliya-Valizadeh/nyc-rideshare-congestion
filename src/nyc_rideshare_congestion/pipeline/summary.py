"""Write the committed record of what was downloaded and loaded.

`reports/load_log.json` lists each load_log row (month, file name, hash, size,
Last-Modified, status) and the hashed reference files. It holds no trip rows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from nyc_rideshare_congestion import config
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
