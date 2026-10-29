"""Tests for the source manifest. No network: HEAD and range reads are faked."""

from __future__ import annotations

import io
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nyc_rideshare_congestion import config
from nyc_rideshare_congestion.sources import manifest


def _parquet_bytes(table: pa.Table) -> bytes:
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


OLD = _parquet_bytes(pa.table({"hvfhs_license_num": ["HV0003"], "trip_miles": [1.5]}))
NEW = _parquet_bytes(
    pa.table({"hvfhs_license_num": ["HV0005"], "trip_miles": [2.0], "cbd_congestion_fee": [1.5]})
)


def _fakes(files: dict[str, bytes]) -> tuple[manifest.HeadFn, manifest.TailFn]:
    def head(url: str) -> dict[str, Any]:
        return {"status": 200, "size_bytes": len(files[url]), "last_modified": "x"}

    def tail(url: str, n: int) -> bytes:
        return files[url][-n:]

    return head, tail


def test_months_spans_year_end() -> None:
    assert config.months("2024-11", "2025-02") == ["2024-11", "2024-12", "2025-01", "2025-02"]
    assert len(config.months(config.FIRST_MONTH, config.LAST_MONTH)) == 27


def test_months_rejects_reversed_range() -> None:
    with pytest.raises(ValueError):
        config.months("2025-02", "2025-01")


def test_footer_schema_reads_columns_only() -> None:
    _, tail = _fakes({"u": NEW})
    cols = manifest.footer_schema("u", tail)
    assert [c[0] for c in cols] == ["hvfhs_license_num", "trip_miles", "cbd_congestion_fee"]
    assert cols[2][1] == "double"


def test_footer_schema_rejects_non_parquet() -> None:
    _, tail = _fakes({"u": b"not a parquet file at all"})
    with pytest.raises(ValueError):
        manifest.footer_schema("u", tail)


def test_build_manifest_groups_schemas_and_checks_disk() -> None:
    month_list = ["2024-12", "2025-01"]
    urls = [config.TRIP_URL.format(month=m) for m in month_list]
    head, tail = _fakes({urls[0]: OLD, urls[1]: NEW})
    total = len(OLD) + len(NEW)
    rows = [{"taxi_zone": "12"}, {"taxi_zone": "4"}]

    out = manifest.build_manifest(month_list, head, tail, rows, free_bytes=total * 2)
    assert out["total_bytes"] == total
    assert out["disk"]["ok"] is True
    assert out["cbd_zones"]["ids"] == [4, 12]
    assert [s["months"] for s in out["schemas"]] == [["2024-12"], ["2025-01"]]
    assert "num_rows" not in str(out)

    short = manifest.build_manifest(month_list, head, tail, rows, free_bytes=total)
    assert short["disk"]["ok"] is False
