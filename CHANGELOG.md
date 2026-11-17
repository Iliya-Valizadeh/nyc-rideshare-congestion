# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and each release matches a
git tag.

## [Unreleased]

### Added

- Project skeleton generated from `ds-project-standard`.
- Download, validation, quarantine and star-schema load for the TLC trip files, with
  the `nrc` command line.
- SQL transforms, the `nrc check` command and the index timing report.
- `nrc demo`, run by `make demo`: the whole pipeline on fake months, offline.
- README for the pipeline half, with the row-count table and both diagrams.
- `nrc panel`, `nrc prechecks` and `nrc estimate`: the Part 2 panel, the stop rules,
  the pre-trend check, the check one year earlier and the main estimate, as
  `docs/eval_plan.md` fixes them. ADR 0008 records how the code reads the open points.
- The Part 2 result in the README, with its checks and the weekly chart.

### Changed

- The template demo modules are archived in `docs/archive/template/`, and
  `docs/ml_test_score.md` now describes this project.
- `make eval` now builds `reports/metrics.json` from the analysis reports, in place of
  the template example.
- `nrc demo` now says at the start and the end that it runs a small synthetic sample in
  seconds, and that the README numbers come from the real TLC files loaded with
  `nrc load --all`.
