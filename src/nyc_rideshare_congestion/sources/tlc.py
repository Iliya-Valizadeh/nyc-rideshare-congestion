"""Download TLC files to data/raw/, hash them and record them in the load log.

This is step one of the pipeline in ADR 0004. One call fetches one monthly trip file:
it checks free disk space against the size from a HEAD request, streams the body to a
`.part` file, checks the byte count, hashes the file with SHA-256, writes the
`load_log` row, and only then renames the `.part` file. A crash at any point leaves
either no file or a file whose hash is already logged, so a re-run resumes cleanly.

Code here never deletes a downloaded file. A file that is fetched again is first moved
to `data/raw/replaced/<name>.<first 8 hash characters>`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import BinaryIO, Protocol

import duckdb

from nyc_rideshare_congestion import config

DiskFreeFn = Callable[[Path], int]


class DownloadError(RuntimeError):
    """A fetch failed or returned the wrong number of bytes."""


class DiskSpaceError(DownloadError):
    """Free disk space is below the file size plus headroom."""


@dataclass(frozen=True)
class Remote:
    """What the server says about a file."""

    size_bytes: int | None
    last_modified: datetime | None  # naive UTC, as DuckDB TIMESTAMP stores it


class Http(Protocol):
    """The two HTTP calls the download needs. Tests pass a fake."""

    def head(self, url: str) -> Remote: ...

    def get(self, url: str, out: BinaryIO) -> Remote: ...


@dataclass(frozen=True)
class Result:
    """What one download call did."""

    name: str
    action: str  # "skipped", "downloaded" or "replaced"
    sha256: str
    size_bytes: int
    path: Path


def parse_http_date(value: str | None) -> datetime | None:
    """Turn an HTTP date header into naive UTC, or None if it is missing or bad."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).replace(tzinfo=None)


def remote_from_headers(content_length: str | None, last_modified: str | None) -> Remote:
    """Build a Remote from the raw Content-Length and Last-Modified headers."""
    size = int(content_length) if content_length and content_length.isdigit() else None
    return Remote(size, parse_http_date(last_modified))


class UrllibHttp:  # pragma: no cover - network
    """The real client, on the standard library."""

    def head(self, url: str) -> Remote:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT_S) as resp:
            return remote_from_headers(
                resp.headers.get("Content-Length"), resp.headers.get("Last-Modified")
            )

    def get(self, url: str, out: BinaryIO) -> Remote:
        with urllib.request.urlopen(url, timeout=config.HTTP_TIMEOUT_S) as resp:
            shutil.copyfileobj(resp, out, config.HASH_CHUNK_BYTES)
            return remote_from_headers(
                resp.headers.get("Content-Length"), resp.headers.get("Last-Modified")
            )


def disk_free(path: Path) -> int:
    """Free bytes on the drive that holds path."""
    return shutil.disk_usage(path).free


def sha256_file(path: Path) -> str:
    """SHA-256 of the whole file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(config.HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def trip_file_name(month: str) -> str:
    return config.TRIP_FILE.format(month=month)


def move_aside(path: Path, sha256: str, replaced_dir: Path) -> Path:
    """Move a file to replaced/<name>.<hash8>, adding .1, .2 ... if that name is taken."""
    replaced_dir.mkdir(parents=True, exist_ok=True)
    target = replaced_dir / f"{path.name}.{sha256[:8]}"
    n = 0
    while target.exists():
        n += 1
        target = replaced_dir / f"{path.name}.{sha256[:8]}.{n}"
    path.rename(target)
    return target


def _fetch(
    url: str, part: Path, http: Http, free_fn: DiskFreeFn
) -> tuple[int, str, datetime | None]:
    """Check disk, stream url to part, check the byte count, hash it, set its mtime."""
    head = http.head(url)
    if head.size_bytes is None:
        raise DownloadError(f"{url}: the server gave no Content-Length")
    need = int(head.size_bytes * (1 + config.DISK_HEADROOM))
    free = free_fn(part.parent)
    if free < need:
        raise DiskSpaceError(
            f"{part.parent}: {free} bytes free, need {need} "
            f"(file {head.size_bytes} plus {config.DISK_HEADROOM:.0%})"
        )
    with part.open("wb") as out:
        got = http.get(url, out)
    written = part.stat().st_size
    expected = got.size_bytes if got.size_bytes is not None else head.size_bytes
    if written != expected:
        raise DownloadError(
            f"{url}: got {written} bytes, expected {expected}. "
            f"{part.name} is kept; run the same command again to fetch it again"
        )
    last_modified = got.last_modified or head.last_modified
    if last_modified is not None:
        stamp = last_modified.replace(tzinfo=UTC).timestamp()
        os.utime(part, (stamp, stamp))
    return written, sha256_file(part), last_modified


def find_row(con: duckdb.DuckDBPyConnection, name: str, sha256: str) -> tuple[int, str] | None:
    """Return (load_id, status) for a file name and hash, or None."""
    row = con.execute(
        "SELECT load_id, status FROM load_log WHERE file_name = ? AND sha256 = ?",
        [name, sha256],
    ).fetchone()
    return None if row is None else (int(row[0]), str(row[1]))


def record_download(
    con: duckdb.DuckDBPyConnection,
    month: str,
    name: str,
    sha256: str,
    size_bytes: int,
    last_modified: datetime | None,
) -> int:
    """Write or update the load_log row for a downloaded file and return its load_id.

    A hash already in the log keeps its row, and a `loaded` row stays `loaded`. A new
    hash gets a new `downloaded` row, and older `downloaded` rows for the same file
    become `replaced`. Older `loaded` rows are left for `load --backfill` (ADR 0004).
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    con.begin()
    try:
        known = find_row(con, name, sha256)
        if known is not None:
            load_id, status = known
            con.execute(
                "UPDATE load_log SET size_bytes = ?, last_modified = ?, downloaded_at = ?, "
                "status = ? WHERE load_id = ?",
                [
                    size_bytes,
                    last_modified,
                    now,
                    "downloaded" if status == "replaced" else status,
                    load_id,
                ],
            )
        else:
            con.execute(
                "UPDATE load_log SET status = 'replaced' "
                "WHERE file_name = ? AND status = 'downloaded'",
                [name],
            )
            row = con.execute(
                "INSERT INTO load_log (file_name, sha256, source_month, size_bytes, "
                "last_modified, status, downloaded_at) "
                "VALUES (?, ?, ?, ?, ?, 'downloaded', ?) RETURNING load_id",
                [name, sha256, month, size_bytes, last_modified, now],
            ).fetchone()
            if row is None:  # pragma: no cover - RETURNING always gives a row
                raise RuntimeError("load_log insert returned no load_id")
            load_id = int(row[0])
        con.commit()
    except BaseException:
        con.rollback()
        raise
    return load_id


def download_month(
    month: str,
    paths: config.Paths,
    con: duckdb.DuckDBPyConnection,
    http: Http,
    *,
    backfill: bool = False,
    free_fn: DiskFreeFn = disk_free,
) -> Result:
    """Fetch one month's trip file unless the same file is already on disk and logged."""
    name = trip_file_name(month)
    target = paths.raw / name
    paths.raw.mkdir(parents=True, exist_ok=True)
    old_hash = sha256_file(target) if target.exists() else None
    if old_hash is not None and not backfill and find_row(con, name, old_hash) is not None:
        return Result(name, "skipped", old_hash, target.stat().st_size, target)

    part = target.with_name(name + ".part")
    size, new_hash, last_modified = _fetch(config.TRIP_URL.format(month=month), part, http, free_fn)
    if old_hash is not None:
        # A backfill, or a file on disk that the log does not know: keep the old copy.
        move_aside(target, old_hash, paths.replaced)
    # Log before the rename. A crash in between leaves no file, so the next run fetches
    # again and finds the same hash in the log.
    record_download(con, month, name, new_hash, size, last_modified)
    os.replace(part, target)
    return Result(name, "replaced" if old_hash else "downloaded", new_hash, size, target)


def download_reference(
    paths: config.Paths, http: Http, *, free_fn: DiskFreeFn = disk_free
) -> list[Result]:
    """Fetch the zone lookup and zone shapes if they are not on disk yet.

    They have no month, so they are not in load_log. `nrc summary` hashes them and
    lists them in reports/load_log.json.
    """
    paths.raw.mkdir(parents=True, exist_ok=True)
    results = []
    for name, url in config.REFERENCE_FILES.items():
        target = paths.raw / name
        if target.exists():
            results.append(
                Result(name, "skipped", sha256_file(target), target.stat().st_size, target)
            )
            continue
        part = target.with_name(name + ".part")
        size, sha, _ = _fetch(url, part, http, free_fn)
        os.replace(part, target)
        results.append(Result(name, "downloaded", sha, size, target))
    return results
