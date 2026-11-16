# 0008: How the analysis code reads the open points in the plan

Date: 2026-11-16. Status: accepted.

This record does not change the plan. It writes down how the code reads the few points
that [eval_plan.md](../eval_plan.md) leaves open. Each choice was made before the
estimate ran.

## Context

The plan fixes the zones, windows, outcomes, estimator, checks and stop rules. A few
details are not spelled out:

- The plan gives size lines for trip volume only. It also says each secondary outcome
  gets both checks "with the same rules".
- The plan asks for a zone interval and a week interval. It does not say how to get a
  week interval for the drift-adjusted estimate. That estimate uses the pre-trend
  months, and those months share days with the before window.
- The plan says the wider interval decides when the two disagree on zero. It does not
  say which interval to test against the smallest effect of interest.
- The stop rule on unknown drop-offs names the before and after windows. The holiday
  list is written for the estimate.
- The event study runs "for each week from October 2024 to March 2025".

## Options

- For the checks on secondary outcomes: apply the trip-volume lines to every outcome,
  or apply them only where the unit matches.
- For the drift-adjusted week interval: draw weeks for the two parts on their own, or
  give the zone interval alone.
- For the size test: use the zone interval, or the wider of the two.

## Decision

- Counts (trip volume for both platforms, Uber alone and Lyft alone) use the plan's
  lines: 0.015 log points of drift for the pre-trend check and 3% for the check one
  year earlier. Fares, durations and the Lyft share have no line in the plan, so their
  checks fail when the interval leaves out zero. This is the strict reading.
- The drift-adjusted estimate gets the zone interval only. Its zone draws are the same
  draws the main estimate and the pre-trend slope use, so their noise is shared.
- The triple difference gets both intervals. Its two windows do not overlap, so one
  week draw covers all four of them.
- The test against the smallest effect of interest uses the wider of the two
  intervals. This is the cautious choice, and it matches the plan's rule on zero.
- The unknown drop-off rule uses the whole windows, as the plan defines them. The
  holiday list applies only to the estimates.
- The event study uses the same days as the main estimate, so the holiday days are
  left out of their weeks. Weeks with no kept days are left out of the chart.
- The share of trips with a fee, and the mean fee, use every day of the after window.

## Consequences

Each check in `reports/pretrend.json` and `reports/placebo.json` states its rule in
words next to its numbers. A reader
who prefers another reading can change one function in
`src/nyc_rideshare_congestion/analysis/` and run `nrc estimate` again.
