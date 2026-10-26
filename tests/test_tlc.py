"""Tests for the download step (ADR 0004 test 16). The HTTP client is a fake."""

from __future__ import annotations

import hashlib
import json
import socket
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import duckdb
import pytest

from nyc_rideshare_congestion import cli, config
from nyc_rideshare_congestion.pipeline import db, summary
from nyc_rideshare_congestion.sources import tlc

LAST_MODIFIED = "Wed, 01 Jan 2025 12:00:00 GMT"
MONTH = "2024-12"
NAME = tlc.trip_file_name(MONTH)
URL = config.TRIP_URL.format(month=MONTH)
PLENTY = 10**12


class FakeHttp:
    """Serves bytes from a dict and records every call."""

    def __init__(self, files: dict[str, bytes], cut: int = 0, length: bool = True) -> None:
        self.files = files
        self.cut = cut  # bytes to drop from each body, to fake a broken transfer
        self.length = length
        self.calls: list[tuple[str, str]] = []

    def _remote(self, url: str) -> tlc.Remote:
        size = str(len(self.files[url])) if self.length else None
        return tlc.remote_from_headers(size, LAST_MODIFIED)

    def head(self, url: str) -> tlc.Remote:
        self.calls.append(("HEAD", url))
        return self._remote(url)

    def get(self, url: str, out: BinaryIO) -> tlc.Remote:
        self.calls.append(("GET", url))
        body = self.files[url]
        out.write(body[: len(body) - self.cut])
        return self._remote(url)


@pytest.fixture
def paths(tmp_path: Path) -> config.Paths:
    return config.Paths(tmp_path)


@pytest.fixture
def con(paths: config.Paths) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = db.connect(paths.db, paths.tmp)
    db.run_schema(connection)
    yield connection
    connection.close()


def _rows(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str, str]]:
    return [
        (str(r[0]), str(r[1]), str(r[2]))
        for r in con.execute(
            "SELECT source_month, sha256, status FROM load_log ORDER BY load_id"
        ).fetchall()
    ]


def test_download_hashes_logs_and_sets_mtime(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    body = b"fake parquet bytes" * 100
    http = FakeHttp({URL: body})
    result = tlc.download_month(MONTH, paths, con, http, free_fn=lambda _: PLENTY)

    target = paths.raw / NAME
    sha = hashlib.sha256(body).hexdigest()
    assert result.action == "downloaded" and result.sha256 == sha
    assert target.read_bytes() == body
    assert not (paths.raw / f"{NAME}.part").exists()
    assert _rows(con) == [(MONTH, sha, "downloaded")]
    row = con.execute("SELECT size_bytes, last_modified FROM load_log").fetchone()
    assert row == (len(body), datetime(2025, 1, 1, 12, 0, 0))
    assert target.stat().st_mtime == datetime(2025, 1, 1, 12, tzinfo=UTC).timestamp()


def test_second_download_skips_without_network(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    http = FakeHttp({URL: b"abc"})
    tlc.download_month(MONTH, paths, con, http, free_fn=lambda _: PLENTY)
    http.calls.clear()
    result = tlc.download_month(MONTH, paths, con, http, free_fn=lambda _: PLENTY)
    assert result.action == "skipped"
    assert http.calls == []
    assert len(_rows(con)) == 1


def test_backfill_with_new_file_keeps_old_copy(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    old, new = b"old file", b"republished file"
    tlc.download_month(MONTH, paths, con, FakeHttp({URL: old}), free_fn=lambda _: PLENTY)
    result = tlc.download_month(
        MONTH, paths, con, FakeHttp({URL: new}), backfill=True, free_fn=lambda _: PLENTY
    )
    old_sha = hashlib.sha256(old).hexdigest()
    new_sha = hashlib.sha256(new).hexdigest()
    assert result.action == "replaced"
    assert (paths.raw / NAME).read_bytes() == new
    assert (paths.replaced / f"{NAME}.{old_sha[:8]}").read_bytes() == old
    assert _rows(con) == [(MONTH, old_sha, "replaced"), (MONTH, new_sha, "downloaded")]


def test_backfill_with_same_file_keeps_one_row(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    body = b"same file"
    for _ in range(3):
        tlc.download_month(
            MONTH, paths, con, FakeHttp({URL: body}), backfill=True, free_fn=lambda _: PLENTY
        )
    sha = hashlib.sha256(body).hexdigest()
    assert _rows(con) == [(MONTH, sha, "downloaded")]
    kept = sorted(p.name for p in paths.replaced.iterdir())
    assert kept == [f"{NAME}.{sha[:8]}", f"{NAME}.{sha[:8]}.1"]


def test_loaded_row_stays_loaded(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    old, new = b"loaded file", b"new file"
    tlc.download_month(MONTH, paths, con, FakeHttp({URL: old}), free_fn=lambda _: PLENTY)
    con.execute("UPDATE load_log SET status = 'loaded'")
    tlc.download_month(
        MONTH, paths, con, FakeHttp({URL: old}), backfill=True, free_fn=lambda _: PLENTY
    )
    assert [r[2] for r in _rows(con)] == ["loaded"]
    tlc.download_month(
        MONTH, paths, con, FakeHttp({URL: new}), backfill=True, free_fn=lambda _: PLENTY
    )
    # The warehouse still holds the old rows until `load --backfill` replaces them.
    assert [r[2] for r in _rows(con)] == ["loaded", "downloaded"]


def test_unlogged_file_on_disk_is_moved_aside_and_fetched(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    paths.raw.mkdir(parents=True)
    (paths.raw / NAME).write_bytes(b"copied in by hand")
    result = tlc.download_month(
        MONTH, paths, con, FakeHttp({URL: b"real"}), free_fn=lambda _: PLENTY
    )
    assert result.action == "replaced"
    assert (paths.raw / NAME).read_bytes() == b"real"
    assert [p.read_bytes() for p in paths.replaced.iterdir()] == [b"copied in by hand"]


def test_short_disk_fetches_nothing(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    body = b"x" * 1000
    http = FakeHttp({URL: body})
    with pytest.raises(tlc.DiskSpaceError):
        tlc.download_month(MONTH, paths, con, http, free_fn=lambda _: 1199)
    assert http.calls == [("HEAD", URL)]
    assert list(paths.raw.iterdir()) == []
    assert _rows(con) == []
    # Exactly the size plus 20% is enough.
    tlc.download_month(MONTH, paths, con, http, free_fn=lambda _: 1200)
    assert len(_rows(con)) == 1


def test_broken_transfer_logs_nothing(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(tlc.DownloadError, match="got 9 bytes, expected 10"):
        tlc.download_month(
            MONTH, paths, con, FakeHttp({URL: b"0123456789"}, cut=1), free_fn=lambda _: PLENTY
        )
    assert not (paths.raw / NAME).exists()
    assert (paths.raw / f"{NAME}.part").exists()
    assert _rows(con) == []
    # The re-run overwrites the .part file and finishes.
    tlc.download_month(MONTH, paths, con, FakeHttp({URL: b"0123456789"}), free_fn=lambda _: PLENTY)
    assert (paths.raw / NAME).read_bytes() == b"0123456789"


def test_missing_content_length_is_an_error(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    with pytest.raises(tlc.DownloadError, match="Content-Length"):
        tlc.download_month(
            MONTH, paths, con, FakeHttp({URL: b"abc"}, length=False), free_fn=lambda _: PLENTY
        )


def test_failed_log_write_rolls_back(paths: config.Paths, con: duckdb.DuckDBPyConnection) -> None:
    tlc.download_month(MONTH, paths, con, FakeHttp({URL: b"first"}), free_fn=lambda _: PLENTY)
    with pytest.raises(duckdb.Error):
        # A month that breaks the CHECK constraint makes the insert fail after the update.
        tlc.record_download(con, "bad", NAME, "0" * 64, 1, None)
    assert [r[2] for r in _rows(con)] == ["downloaded"]


def test_reference_files_download_once(paths: config.Paths) -> None:
    files = {url: name.encode() for name, url in config.REFERENCE_FILES.items()}
    http = FakeHttp(files)
    first = tlc.download_reference(paths, http, free_fn=lambda _: PLENTY)
    assert [r.action for r in first] == ["downloaded", "downloaded"]
    http.calls.clear()
    second = tlc.download_reference(paths, http, free_fn=lambda _: PLENTY)
    assert [r.action for r in second] == ["skipped", "skipped"]
    assert http.calls == []


def test_parse_http_date() -> None:
    assert tlc.parse_http_date(None) is None
    assert tlc.parse_http_date("not a date") is None
    assert tlc.parse_http_date("Wed, 01 Jan 2025 07:00:00 -0500") == datetime(2025, 1, 1, 12)
    assert tlc.parse_http_date("Wed, 01 Jan 2025 12:00:00") == datetime(2025, 1, 1, 12)
    assert tlc.remote_from_headers("12x", None) == tlc.Remote(None, None)


def test_summary_lists_rows_and_reference_files(
    paths: config.Paths, con: duckdb.DuckDBPyConnection
) -> None:
    tlc.download_month(MONTH, paths, con, FakeHttp({URL: b"abc"}), free_fn=lambda _: PLENTY)
    files = {url: b"zones" for url in config.REFERENCE_FILES.values()}
    tlc.download_reference(paths, FakeHttp(files), free_fn=lambda _: PLENTY)
    out = summary.write_load_log(con, paths)
    record = json.loads(out.read_text(encoding="utf-8"))
    assert record["load_log"] == [
        {
            "month": MONTH,
            "file_name": NAME,
            "sha256": hashlib.sha256(b"abc").hexdigest(),
            "size_bytes": 3,
            "last_modified": "2025-01-01T12:00:00Z",
            "status": "downloaded",
        }
    ]
    assert MONTH not in record["months_missing"]
    assert len(record["months_missing"]) == len(config.study_months()) - 1
    assert [f["file_name"] for f in record["reference_files"]] == list(config.REFERENCE_FILES)


def test_cli_download_month_and_summary(paths: config.Paths) -> None:
    http = FakeHttp({URL: b"abc"})
    root = ["--root", str(paths.root)]
    assert cli.main([*root, "download", "--month", MONTH], http, lambda _: PLENTY) == 0
    assert cli.main([*root, "download", "--month", MONTH], http, lambda _: PLENTY) == 0
    assert [c[0] for c in http.calls] == ["HEAD", "GET"]
    assert cli.main([*root, "summary"]) == 0
    assert (paths.reports / "load_log.json").exists()


def test_cli_exit_codes(paths: config.Paths, capsys: pytest.CaptureFixture[str]) -> None:
    root = ["--root", str(paths.root)]
    http = FakeHttp({URL: b"x" * 100})
    assert cli.main([*root, "download", "--month", MONTH], http, lambda _: 10) == 4
    assert "FAIL disk" in capsys.readouterr().out
    broken = FakeHttp({URL: b"x" * 100}, cut=5)
    assert cli.main([*root, "download", "--backfill", MONTH], broken, lambda _: PLENTY) == 1
    for bad in ("2022-12", "2025-04", "24-12", "2024-1"):
        with pytest.raises(SystemExit) as err:
            cli.main([*root, "download", "--month", bad])
        assert err.value.code == 2


def test_cli_all_follows_download_order(paths: config.Paths) -> None:
    files = {config.TRIP_URL.format(month=m): m.encode() for m in config.study_months()}
    files |= {url: b"ref" for url in config.REFERENCE_FILES.values()}
    http = FakeHttp(files)
    assert cli.main(["--root", str(paths.root), "download", "--all"], http, lambda _: PLENTY) == 0
    got = [url for verb, url in http.calls if verb == "GET"]
    want = list(config.REFERENCE_FILES.values()) + [
        config.TRIP_URL.format(month=m) for m in config.download_order()
    ]
    assert got == want


def test_download_order_matches_adr_0001() -> None:
    order = config.download_order()
    assert order[:6] == config.months("2024-10", "2025-03")
    assert order[6:12] == config.months("2023-10", "2024-03")
    assert sorted(order) == config.study_months()
    assert len(order) == len(set(order)) == 27


def test_sockets_are_blocked() -> None:
    with pytest.raises(RuntimeError, match="network"):
        socket.create_connection(("example.com", 80))
    with pytest.raises(RuntimeError, match="network"):
        urllib.request.urlopen("http://example.com", timeout=1)
