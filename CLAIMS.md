# Claims

Every number in this repo's Markdown files has a row here. Each row says where the
number comes from and which command makes that file. `make check-docs` fails if a
number in the docs has no row, or if a row does not match its source.

Small whole numbers (0 to 10), years, dates and version numbers are skipped. To skip
one line by hand, add the comment `<!-- not-a-claim -->` to it.

| Claim | Value | Source | Command |
|---|---|---|---|
| Monthly HVFHV files fetched, 2023-01 to 2025-03 | 27 | `reports/source_manifest.json#file_count` | `uv run python -m nyc_rideshare_congestion.sources.manifest` |
| Total size of those files (GB) | 13.05 | `reports/source_manifest.json#total_gb` | `uv run python -m nyc_rideshare_congestion.sources.manifest` |
| Disk headroom over the download size | 20% | `reports/source_manifest.json#disk.headroom` | `uv run python -m nyc_rideshare_congestion.sources.manifest` |
| Disk need with headroom (GB) | 15.66 | `reports/source_manifest.json#disk.need_gb` | `uv run python -m nyc_rideshare_congestion.sources.manifest` |
| Free disk space when checked (GB) | 278.24 | `reports/source_manifest.json#disk.free_gb` | `uv run python -m nyc_rideshare_congestion.sources.manifest` |
| Taxi zones in the MTA congestion zone list | 38 | `reports/source_manifest.json#cbd_zones.count` | `uv run python -m nyc_rideshare_congestion.sources.manifest` |

Example row, for the format only:
`| Test AUC | 0.58 (0.55 to 0.61) | reports/metrics.json#auc | make eval |`
