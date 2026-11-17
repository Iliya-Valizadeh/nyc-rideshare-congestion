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
| Rows in the 45 downloaded files | 904,724,544 | `reports/load_summary.json#totals.rows_in_file` | `uv run nrc summary` |
| Rows kept, all 45 months | 904,515,562 | `reports/load_summary.json#totals.rows_kept` | `uv run nrc summary` |
| Rows quarantined, all 45 months | 208,982 | `reports/load_summary.json#totals.rows_quarantined` | `uv run nrc summary` |
| Rows in the fact table, all 45 months | 407,762,234 | `reports/load_summary.json#totals.fact_rows` | `uv run nrc summary` |
| Quarantined share of all rows | 0.0231% | `reports/load_summary.json#totals.quarantine_share` | `uv run nrc summary` |
| Rows quarantined as NEGATIVE_FARE | 167,982 | `reports/load_summary.json#totals.quarantined_by_reason.NEGATIVE_FARE` | `uv run nrc summary` |
| Rows quarantined as IMPOSSIBLE_DURATION | 36,943 | `reports/load_summary.json#totals.quarantined_by_reason.IMPOSSIBLE_DURATION` | `uv run nrc summary` |
| Rows quarantined as DUPLICATE_TRIP | 4,057 | `reports/load_summary.json#totals.quarantined_by_reason.DUPLICATE_TRIP` | `uv run nrc summary` |
| Plain estimate: change in trips into the zone against comparison zones, wider 95% interval | 7.8% lower (10.3% to 4.6% lower) | `reports/metrics.json#main` | `uv run nrc estimate`, then `make eval` |
| Check one year earlier: same comparison with no toll, wider interval | 7.9% lower (10.6% to 4.6% lower) | `reports/metrics.json#placebo_check` | `uv run nrc estimate`, then `make eval` |
| [Triple difference](docs/glossary.md#triple-difference), log points, wider interval | 0.0007 (0.0476 lower to 0.0431 higher) | `reports/metrics.json#triple_difference` | `uv run nrc estimate`, then `make eval` |
| Pre-trend drift over three months and the plan's line, log points | 0.0139, line 0.015 | `reports/metrics.json#pretrend_check` | `uv run nrc estimate`, then `make eval` |
| Drift-adjusted plain estimate | 6.6% lower | `reports/metrics.json#drift_adjusted` | `uv run nrc estimate`, then `make eval` |
| Share of treated trips with the toll after the start, mean toll, share of comparison trips with the toll | 98.3%, $1.47, 0.6% | `reports/metrics.json#fees_after_start` | `uv run nrc estimate`, then `make eval` |
| Comparison zones: other Manhattan zones and Brooklyn zones | 22 and 61 | `reports/metrics.json#zones` | `uv run nrc estimate`, then `make eval` |
| Days kept before and after the start | 79 and 84 | `reports/metrics.json#days_kept` | `uv run nrc estimate`, then `make eval` |
| [Bootstrap](docs/glossary.md#bootstrap) rounds, seed and interval level | 2,000, seed 20250105, 95% | `reports/metrics.json#bootstrap` | `uv run nrc estimate`, then `make eval` |
| [Smallest effect of interest](docs/glossary.md#smallest-effect-of-interest) from the plan | 3% | `reports/metrics.json#verdict` | `uv run nrc estimate`, then `make eval` |
| Months loaded, January 2023 to September 2026 | 45 | `reports/load_summary.json#months_loaded` | `uv run nrc summary` |
| Part 3, 2025 against 2024: change in trips into the zone against comparison zones, wider 95% interval | 6.7% lower (7.8% to 5.6% lower) | `reports/extended.json#year_over_year.outcomes.trips_per_day_both_platforms` | `uv run nrc extended` |
| Part 3, check a year earlier, 2024 against 2023, wider interval | 5.4% lower (6.4% to 4.5% lower) | `reports/extended.json#check_one_year_earlier.outcomes.trips_per_day_both_platforms` | `uv run nrc extended` |
| Part 3, triple difference as a percent, interval by zone (the wider) | 1.4% lower (2.7% lower to 0.2% higher) | `reports/extended.json#triple_difference.trips_per_day_both_platforms` | `uv run nrc extended` |
| Part 3, exploratory, 2026 against 2025, wider interval | 10.9% lower (12.8% to 9.2% lower) | `reports/extended.json#second_fee_year.outcomes.trips_per_day_both_platforms` | `uv run nrc extended` |
| Part 3, smallest effect of interest | 3% | `reports/extended.json#verdict` | `uv run nrc extended` |
| Part 3 bootstrap rounds | 2,000 | `reports/extended.json#bootstrap` | `uv run nrc extended` |

Example row, for the format only:
`| Test AUC | 0.58 (0.55 to 0.61) | reports/metrics.json#auc | make eval |`
