"""Build the source manifest from file metadata only.

For each monthly HVFHV file it sends one HTTP HEAD request (size, last modified) and
reads the Parquet footer with two small range requests (column names and types). It
never downloads trip rows and never records row counts. It also fetches the MTA list
of taxi zones inside the Central Business District (the Congestion Relief Zone) and
checks free disk space against the download size plus headroom.

Usage: python -m nyc_rideshare_congestion.sources.manifest [--out reports/source_manifest.json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from nyc_rideshare_congestion.config import (
    CBD_ZONES_URL,
    FIRST_MONTH,
    LAST_MONTH,
    TRIP_URL,
    ZONE_LOOKUP_URL,
    ZONE_SHAPES_URL,
    months,
)
from nyc_rideshare_congestion.config import DISK_HEADROOM as HEADROOM
from nyc_rideshare_congestion.config import HTTP_TIMEOUT_S as TIMEOUT_S

GB = 10**9

HeadFn = Callable[[str], dict[str, Any]]
TailFn = Callable[[str, int], bytes]


def http_head(url: str) -> dict[str, Any]:  # pragma: no cover - network
    """Return status, size and last-modified for a URL, without the body."""
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return {
            "status": resp.status,
            "size_bytes": int(resp.headers["Content-Length"]),
            "last_modified": resp.headers.get("Last-Modified", ""),
        }


def http_tail(url: str, n: int) -> bytes:  # pragma: no cover - network
    """Return the last n bytes of a URL with a suffix range request."""
    req = urllib.request.Request(url, headers={"Range": f"bytes=-{n}"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        data: bytes = resp.read()
    if len(data) != n:
        raise ValueError(f"asked for {n} bytes from {url}, got {len(data)}")
    return data


def http_json(url: str) -> Any:  # pragma: no cover - network
    """Return the parsed JSON body of a URL."""
    with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read())


def footer_schema(url: str, tail: TailFn) -> list[list[str]]:
    """Read only the Parquet footer and return [name, type] for each column."""
    end = tail(url, 8)
    if end[4:] != b"PAR1":
        raise ValueError(f"not a Parquet file: {url}")
    meta_len = int.from_bytes(end[:4], "little")
    footer = tail(url, meta_len + 8)
    # The footer is self-contained, so a fake file of magic + footer is enough to read
    # the schema. Row counts in the footer are never read out.
    schema = pq.read_schema(pa.BufferReader(b"PAR1" + footer))
    return [[field.name, str(field.type)] for field in schema]


def build_manifest(
    month_list: list[str],
    head: HeadFn,
    tail: TailFn,
    cbd_rows: list[dict[str, Any]],
    free_bytes: int,
) -> dict[str, Any]:
    """Collect sizes, schemas, the CBD zone list and the disk check into one dict."""
    files = []
    schemas: list[list[list[str]]] = []
    for month in month_list:
        url = TRIP_URL.format(month=month)
        info = head(url)
        columns = footer_schema(url, tail)
        if columns not in schemas:
            schemas.append(columns)
        files.append({"month": month, "url": url, **info, "schema_id": schemas.index(columns)})
    total = sum(int(f["size_bytes"]) for f in files)
    need = total * (1 + HEADROOM)
    zone_ids = sorted(int(row["taxi_zone"]) for row in cbd_rows)
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "first_month": month_list[0],
        "last_month": month_list[-1],
        "file_count": len(files),
        "total_bytes": total,
        "total_gb": round(total / GB, 2),
        "disk": {
            "headroom": HEADROOM,
            "need_gb": round(need / GB, 2),
            "free_gb": round(free_bytes / GB, 2),
            "ok": free_bytes >= need,
        },
        "zone_lookup_url": ZONE_LOOKUP_URL,
        "zone_shapes_url": ZONE_SHAPES_URL,
        "cbd_zones": {"url": CBD_ZONES_URL, "count": len(zone_ids), "ids": zone_ids},
        "schemas": [
            {
                "schema_id": i,
                "months": [f["month"] for f in files if f["schema_id"] == i],
                "columns": s,
            }
            for i, s in enumerate(schemas)
        ],
        "files": files,
    }


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - network
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="reports/source_manifest.json")
    parser.add_argument("--first", default=FIRST_MONTH)
    parser.add_argument("--last", default=LAST_MONTH)
    args = parser.parse_args(argv)
    out = Path(args.out)
    free = shutil.disk_usage(out.resolve().anchor).free
    manifest = build_manifest(
        months(args.first, args.last), http_head, http_tail, http_json(CBD_ZONES_URL), free
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    disk = manifest["disk"]
    print(f"{manifest['file_count']} files, {manifest['total_gb']} GB; disk ok: {disk['ok']}")
    return 0 if disk["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
