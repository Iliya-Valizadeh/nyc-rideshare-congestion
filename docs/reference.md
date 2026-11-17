# Reference

Facts to look up: commands, files and settings.

## Make targets

| Target | What it does |
|---|---|
| `make setup` | Installs the pinned packages with `uv` |
| `make lint` | Runs ruff and mypy |
| `make test` | Runs pytest with a coverage report for `src/` |
| `make eval` | Builds `reports/metrics.json` from the analysis reports |
| `make demo` | Runs a short demo that needs no downloads or keys |
| `make check-docs` | Runs the claims, readability, writing-style, link and README checks |
| `make all` | Runs every step above in order |

## Analysis commands

| Command | What it does |
|---|---|
| `nrc panel` | Builds the analysis panel in the warehouse |
| `nrc prechecks` | Runs the stop rules, the pre-trend check and the check one year earlier |
| `nrc estimate` | Runs the main estimate in `docs/eval_plan.md` |
| `nrc extended` | Runs the longer window, compared year over year (ADR 0009) |

## Output files

| File | What it holds |
|---|---|
| `reports/metrics.json` | The headline numbers, read by `CLAIMS.md` and the portfolio site |
| `reports/stop_rules.json` | Whether each stop rule passed |
| `reports/pretrend.json` and `reports/placebo.json` | The pre-trend check and the check one year earlier |
| `reports/estimate.json` | The main estimate, its secondary versions and the verdict |
| `reports/load_log.json` and `reports/load_summary.json` | What was downloaded and loaded |
| `reports/index_timing.json` | The timing of the drop-off zone index |

## Package

| Module | What it does |
|---|---|
| `pipeline/` | Validates, quarantines, loads, transforms and checks the trip files |
| `sources/` | Downloads the TLC files and builds the source manifest. The only code that uses the network |
| `analysis/` | Builds the panel, runs the stop rules and the estimates, and draws the charts |
| `cli.py` | The `nrc` command line |
| `config.py` | Paths, months, URLs and limits |
| `params.py` | Reads the parameter block in `docs/eval_plan.md` |
| `evaluate.py` | Builds `reports/metrics.json` from the analysis reports, for `make eval` |
| `demo.py` | Runs the pipeline on fake months for `make demo`, with no downloads |
