# What's weak

The full list of limits. The README shows the top few. Rank them by how much each one
could change the main result, with the largest first. Say what was done about each
one, even if the answer is "nothing yet".

| Rank | Weakness | How much it could change the result | What was done |
|---|---|---|---|
| 1 | The quality rules catch only values that cannot be true, such as negative fares. Possible but strange values, such as a very long trip with a normal fare, are kept | Strange rows that are kept add noise to fares and durations. Trip counts change less | Nothing yet. The Part 2 analysis will report how outliers are handled |
| 2 | The numbers describe the files as the TLC published them on the download dates in `reports/load_log.json` | A later TLC fix to a file would change the counts for that month | `nrc check` flags a raw file that changed, and `nrc load --backfill` reloads it |
| 3 | Everything runs on one laptop with a 2 GB DuckDB memory cap. The full-table index ran out of memory | Changes speed only, not the counts | The index was timed on a three-month slice instead ([ADR 0007](decisions/0007-transforms-check-and-index.md)) |
| 4 | No foreign key clauses, because DuckDB foreign keys block the reload deletes | An orphan key would not be stopped by the database | `nrc check` tests every fact key against its dimension |
| 5 | Part 2, the congestion pricing study, has no results yet | Not applicable yet | The plan is fixed in [eval_plan.md](eval_plan.md); its weaknesses get added here with the results |
