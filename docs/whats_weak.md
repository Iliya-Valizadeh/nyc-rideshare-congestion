# What's weak

The full list of limits. The README shows the top few. Rank them by how much each one
could change the main result, with the largest first. Say what was done about each
one, even if the answer is "nothing yet".

| Rank | Weakness | How much it could change the result | What was done |
|---|---|---|---|
| 1 | The check one year earlier fails. With no toll, trips into the zone fell 7.9% against the comparison zones from fall to winter | Large. The same fall shows up a year earlier with no toll, so the plain estimate of 7.8% lower cannot be read as the toll | The headline is the [triple difference](glossary.md#triple-difference), as the plan says. It assumes the season gap repeats each year, and only one earlier winter is in the data |
| 2 | The pre-trend check passes by a small margin: a drift of 0.0139 log points against a line of 0.015, and the slope's interval leaves out zero | Medium. Taking the drift out moves the plain estimate from 7.8% lower to 6.6% lower | The drift-adjusted estimate is in `reports/estimate.json`, as the plan asks |
| 3 | The interval that draws whole weeks is much wider than the one that draws zones | Medium. Shocks shared by all zones, such as weather, are a large part of the noise | The README quotes the wider interval, as the plan says |
| 4 | The quality rules catch only values that cannot be true, such as negative fares. Possible but strange values, such as a very long trip with a normal fare, are kept | Strange rows that are kept add noise to fares and durations. Trip counts change less | Nothing yet. The estimates add up all kept rows, so strange values stay in the fare and trip-time sums |
| 5 | The numbers describe the files as the TLC published them on the download dates in `reports/load_log.json` | A later TLC fix to a file would change the counts for that month | `nrc check` flags a raw file that changed, and `nrc load --backfill` reloads it |
| 6 | Everything runs on one laptop with a 2 GB DuckDB memory cap. The full-table index ran out of memory | Changes speed only, not the counts | The index was timed on a three-month slice instead ([ADR 0007](decisions/0007-transforms-check-and-index.md)) |
| 7 | No foreign key clauses, because DuckDB foreign keys block the reload deletes | An orphan key would not be stopped by the database | `nrc check` tests every fact key against its dimension |
| 8 | The longer window was planned after the first result was known ([ADR 0009](decisions/0009-deviation-longer-window.md)). It is a deviation from the plan | Medium. The design choices could lean on what the first result showed, though no data after March 2025 had been seen | The record was committed before any new month was loaded. The plan's own result stays the primary result and the new one is labelled as an amendment |
