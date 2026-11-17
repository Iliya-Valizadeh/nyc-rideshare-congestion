# ML Test Score self-assessment

This page scores this repo against the ML Test Score, a checklist from
[Breck et al. (Google, 2017)][breck]. The checklist was written for machine learning
systems that serve live predictions. This repo is a data pipeline plus a causal study.
It deploys no model. Many tests therefore do not apply, and they earn no points.

## In plain words

Google wrote a list of 28 tests for a machine learning system. <!-- not-a-claim -->
A system should pass them before people rely on it.
This page checks this repo against that list. The data pipeline and the study code are
well tested, and the overall score is one. The lowest section is monitoring. Nothing
here watches the data or the code after a change, apart from checks that someone runs
by hand. A test that does not apply to a study with no live model is marked
"does not apply" and counts as zero. The score is low partly for that reason.

## How the scoring works

The paper gives each test a score:

- none: the test is not done
- half a point: the test is done by hand, and the result is written down
- one point: the test is automated and runs again on every change

A section's score is the sum of its seven tests. The overall score is the lowest of
the four section scores, so one weak section pulls the whole system down. The paper
reads an overall score of zero as closer to a research project than a production
system.

Here, a test that does not apply scores zero and says why. It is not counted as passed.

## What is scored

The pipeline downloads the TLC trip files, checks them, quarantines bad rows and loads
a DuckDB star schema. The study then runs a pre-registered
[difference-in-differences](glossary.md#difference-in-differences) estimate of the
effect of congestion pricing on rideshare trips. The code is in `src/` and the tests
are in `tests/`.

CI (continuous integration, the checks GitHub runs on every push) runs `make lint`,
`make test`, `make check-docs` and `make demo`, as set in `.github/workflows/ci.yml`.
CI uses fake data, so it checks the code but not the real warehouse. A test counts as
automated when CI runs it.

## Summary

| Section | Score |
|---|---|
| Features and data | three |
| Model development | one and a half |
| Infrastructure | three |
| Monitoring | one |
| Overall (the lowest section) | one |

The weakest items are the monitoring tests. No tool reports new releases of
dependencies. The real data is checked only when someone runs `nrc check`. Speed and
memory use are measured once by hand.

## Tests for features and data

| # | Test | Score | Evidence or gap |
|---|---|---|---|
| 1 | Feature expectations are captured in a schema | one | The load checks the file schema and each row against fixed rules, and bad rows go to a quarantine table with a reason code. Rules: `src/nyc_rideshare_congestion/pipeline/schema.py` and `rules.py`. Tests: `tests/test_load.py`, `tests/test_manifest.py` |
| 2 | All features are beneficial | does not apply | The study has no predictive features. The outcome and the comparison groups are fixed in `docs/eval_plan.md` |
| 3 | No feature's cost is too much | does not apply | There is no feature set to prune. Query cost is measured under monitoring item 6 |
| 4 | Features adhere to meta-level requirements | half | `DATASHEET.md` records the source terms and what the data may be used for. No test checks it |
| 5 | The data pipeline has appropriate privacy controls | half | `DATASHEET.md` states that the records carry no rider or driver IDs. No automated check confirms this on a new file |
| 6 | New features can be added quickly | does not apply | The study has no feature set. A new month is added with `nrc load` |
| 7 | All input feature code is tested | one | Loading, row rules, transforms and the panel are tested: `tests/test_load.py`, `tests/test_transforms.py`, `tests/test_analysis_warehouse.py` |

## Tests for model development

| # | Test | Score | Evidence or gap |
|---|---|---|---|
| 1 | Model specs (the code that defines a model) are reviewed and checked in | half | The plan, `docs/eval_plan.md`, and the ADRs in `docs/decisions/` are in git, and a change to the plan is recorded as a deviation (ADR 0009). No second person has reviewed them |
| 2 | Offline and online metrics correlate | does not apply | There is no online use |
| 3 | All hyperparameters have been tuned | does not apply | The windows and zone groups are fixed by the plan before the data was analysed. Nothing is tuned |
| 4 | The impact of model staleness is known | does not apply | There is no deployed model to go stale. The drift over time is measured by the check one year earlier (ADR 0009) |
| 5 | A simpler model is not better | half | The plain estimate, the drift-adjusted estimate and the [triple difference](glossary.md#triple-difference) are all reported in `reports/estimate.json`, so the simplest one can be read next to the others. No test compares them on real data |
| 6 | Model quality is sufficient on important data slices | half | `reports/estimate.json` reports the estimate in parts, and the [event study](glossary.md#event-study) shows the comparison week by week. No rule fails the build when a slice looks wrong |
| 7 | The model is tested for considerations of inclusion | does not apply | The data describes trips between zones, not people |

## Tests for infrastructure

| # | Test | Score | Evidence or gap |
|---|---|---|---|
| 1 | Training is reproducible | one | The [bootstrap](glossary.md#bootstrap) uses the plan's seed (`src/nyc_rideshare_congestion/analysis/bootstrap.py`). `uv.lock` pins packages, and CI installs with `uv sync --locked`. `tests/test_pipeline.py` rebuilds `reports/metrics.json` from the committed reports and compares |
| 2 | Model specs are unit tested | one | The estimator is tested on fake data with a known effect and with no effect: `tests/test_estimator.py`, `tests/test_extended.py` |
| 3 | The ML pipeline is integration tested | one | `tests/test_demo.py` runs the whole pipeline on fake months. `tests/test_analysis_warehouse.py` runs the study commands end to end |
| 4 | Model quality is validated before serving | does not apply | There is no serving step. The stop rules in `src/nyc_rideshare_congestion/analysis/stop_rules.py` check the data before the estimate is read |
| 5 | The model is debuggable | none | No tool follows one zone-day from the raw file to the estimate. The quarantine table explains dropped rows only |
| 6 | Models are canaried before serving | does not apply | There is no serving step |
| 7 | Serving models can be rolled back | does not apply | There is no serving step. A load can be rerun, and the transforms are safe to run twice (`tests/test_transforms.py`) |

## Monitoring tests

| # | Test | Score | Evidence or gap |
|---|---|---|---|
| 1 | Dependency changes result in notification | none | Versions are pinned in `uv.lock`, but there is no dependency bot in `.github/` |
| 2 | Data invariants hold for inputs | half | `nrc check` tests the warehouse for missing months, counts that do not add up, orphan keys and stale files (`tests/test_checks.py`). On the real warehouse someone must run it |
| 3 | Training and serving are not skewed | does not apply | There is no serving step |
| 4 | Models are not too stale | does not apply | There is no deployed model |
| 5 | Models are numerically stable | none | The estimator refuses a panel with missing days, and the bootstrap refuses draws with missing or infinite values (`src/nyc_rideshare_congestion/analysis/bootstrap.py`). These guards run inside the analysis. Nothing watches the reported numbers after a run |
| 6 | Computing performance has not regressed | half | `reports/index_timing.json` records speed and memory by hand. No automated check compares it with an earlier run |
| 7 | Prediction quality has not regressed | does not apply | There are no served predictions. The test that rebuilds `reports/metrics.json` checks that two files agree, not that the estimate held |

[breck]: https://research.google/pubs/the-ml-test-score-a-rubric-for-ml-production-readiness-and-technical-debt-reduction/
