# 0005: Download details that ADR 0004 leaves open

Date: 2026-10-26. Status: accepted.

## Context

ADR 0004 sets the download step: fetch to a `.part` file, hash it, log it, skip a file
that is already logged, check the disk first, and move old files aside on
`--backfill`. Building it (`sources/tlc.py`) raised a few questions that ADR 0004 does
not answer.

## Options

- Rename the `.part` file first, then write the log row. A crash in between leaves a
  file on disk that the log does not know. Rejected.
- Write the log row first, then rename. A crash in between leaves no file, so the next
  run fetches again, gets the same hash and updates the same row. Chosen.

## Decision

Write the log row first, then rename the `.part` file. The other open questions:

| Question | Answer |
|---|---|
| A file is on disk but the log does not know it (copied in by hand, for example) | Treat it like a backfill. It moves to `data/raw/replaced/` and the file is fetched again. It is never trusted without a fresh download |
| The same file comes back with a new hash | It gets a new `downloaded` row. Older `downloaded` rows for that file become `replaced`, because their file has moved aside. Older `loaded` rows stay `loaded`: the warehouse still holds their rows until `load --backfill` swaps them (ADR 0004) |
| A name is already taken in `replaced/` | Extra copies get `.1`, `.2` and so on. Nothing is overwritten |
| The file's modified time | Set to its `Last-Modified` time, so the freshness check in T07 can compare the two |
| The zone lookup and zone shapes | They have no month, and `load_log.source_month` is required, so they are not in `load_log`. `nrc download --reference` fetches them once. `nrc summary` lists their hash, size and modified time in `reports/load_log.json` |
| The MTA zone list | Not fetched again. `reports/source_manifest.json` already holds it, and the plan's zone list is fixed |
| Which months are allowed | `--month` and `--backfill` accept only the study's months (ADR 0001). Anything else exits with code 2 |
| A failed or short transfer | Exits with code 1 and keeps the `.part` file. The next run writes over it |
| "2GB" in ADR 0004 | Set as `2000MB`. DuckDB prints it in GiB, so the number it shows looks a little lower |

## Consequences

- Any download command can be run again after a crash or a network error.
- A hand-copied file costs one extra download, but the log only ever holds hashes of
  files this code fetched.
- `reports/load_log.json` holds file metadata only: names, hashes, sizes, dates and
  status. No trip rows.
