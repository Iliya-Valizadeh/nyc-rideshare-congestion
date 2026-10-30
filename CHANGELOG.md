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

### Changed

- `nrc demo` now says at the start and the end that it runs a small synthetic sample in
  seconds, and that the README numbers come from the real TLC files loaded with
  `nrc load --all`.
