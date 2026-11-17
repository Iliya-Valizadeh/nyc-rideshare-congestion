# 0009: Deviation: a longer window, compared year over year

Date: 2026-11-16. Status: accepted.

This record amends [eval_plan.md](../eval_plan.md). It was written and committed before
any file after March 2025 was loaded, and before any trip was counted by zone or by
month in those files. The commit that adds this file is the proof.

## In plain words

The first study used three months after the fee started. The TLC has since published
many more months. This record says how those months will be used, before anyone looks
at them. The new comparison sets each month of 2025 against the same month of 2024. Both
sides of that comparison sit in the same season, so the season can no longer pass for
the fee. The first study's result stays as it is. The new result is reported next to
it and is labelled as an amendment.

## Context

- The plan's study ended in March 2025. Its result is already reported: the check one
  year earlier failed, so the plan's headline is the
  [triple difference](../glossary.md#triple-difference), and it is not clear.
- That check failed because trips into the zone fall against the comparison zones from
  fall to winter in a year with no fee. A before window in fall and an after window in
  winter mix the fee with the season.
- The [pre-trend](../glossary.md#parallel-trends) check found a steady drift in the same
  direction. It passed by a small margin.
- I know those results. I have not seen any data after March 2025. So the choices
  below were shaped by the first result. They were not shaped by the new months.

## Options

- Keep the plan's fall-before and winter-after design and only stretch the after
  window. This makes the season problem worse, because the after window would then
  cover spring and summer too.
- Compare each month with the same month one year earlier. The season is the same on
  both sides. A steady drift between the two groups does not cancel, so it has to be
  measured in a year with no fee and taken out.
- Fit a model with zone and month effects on zone-level rows. This weighs every zone
  the same, which answers a different question from the plan's (all trips into the
  zone).

## Decision

The second option. Everything not named here stays as the plan sets it: the zone
groups, which trips count, the unit, the outcomes, the two
[smallest effect of interest](../glossary.md#smallest-effect-of-interest), the two
[bootstrap](../glossary.md#bootstrap) methods, the rounds, the seed and the interval level.

### Data

Every monthly file the TLC has published after March 2025 is loaded through the same
pipeline. On the day of this record that is April 2025 to September 2026. The stop
rules and the pipeline checks apply to every loaded month.

### Year-over-year estimate

- After window: 2025-01-05 to 2025-12-31.
- Before window: 2024-01-05 to 2024-12-31. No fee on any day.
- Days kept: every day in both windows. The holidays fall in both years, so none is
  left out. The days from January 1 to January 4 are outside both windows.
- Formula: the plan's ratio of ratios, on mean trips per day. For fares, durations
  and the Lyft share: the plan's differences.
- Check one year earlier: the same estimate on 2024-01-05 to 2024-12-31 against
  2023-01-05 to 2023-12-31. Neither year has a fee. It fails by the plan's rule: the
  interval leaves out zero and the size is at least the smallest effect of interest.
- Lead number: the triple difference, which is the year-over-year estimate minus the
  check one year earlier, on the log scale. It leads whatever the check shows. The
  reason is the drift the first study already found. A drift of that size over twelve
  months would be larger than the smallest effect of interest, so the plain
  year-over-year number is expected to carry it. The triple difference takes out a
  drift that is the same from one year to the next. It does not take out a drift that
  speeds up or slows down. Only one earlier pair of years is in the data, so this
  cannot be tested.
- Intervals: the zone bootstrap, with the same draws as every other estimate. The
  week bootstrap draws calendar weeks inside each window. If the two disagree on
  zero, the wider one decides, as in the plan. The standard error from the zone
  draws is reported next to the interval.

### Event study by month

- Series: for each month from January 2023 to the last loaded month, the log of
  treated trips over control trips (mean per day). January 2025 uses the days from
  January 5.
- Chart: for each month from January 2024 on, that value minus the value twelve
  months earlier, with a band from the zone bootstrap.
- How to read it: the months of 2024 compare two years with no fee. The months of
  2025 compare a fee year with a year with no fee. The months of 2026 compare two fee
  years, so a one-time step would bring the line back to its 2024 level.
- This chart is for reading the pattern. It is not a second test.

### Second fee year

The estimate for 2026-01-05 to the last loaded day against the same days of 2025 is
reported as a description. Both windows have the fee. It is labelled exploratory.

### Zone map

For each zone: the change in the log of trips per day from the before window to the
after window of the year-over-year estimate, minus the same change for all control
zones together. It is a description of where the change sits. No zone gets an
interval or a test.

### Stop rules for the new windows

The plan's stop rules apply with the year-over-year windows in place of the plan's
windows: the quarantine share for every loaded month, the quarantine gap between the
groups for every month in the windows, the change in the share of unknown drop-offs
between the before and after windows, and no day with zero rows for a platform. If a
rule fails, no year-over-year outcome is reported.

### What may be said

The plan's table applies to the triple difference of the year-over-year estimate. The
plan's own result stays the study's primary result, because it was fixed before any
result existed. The year-over-year result is reported next to it and labelled
"amendment". If the two disagree, the memo says so and does not pick the nicer one.

## Consequences

- The season no longer sits inside the comparison.
- The result leans on one assumption: the drift between the groups from 2024 to 2025
  would have been the same as from 2023 to 2024 without the fee.
- Anything else that changed in the zone during 2025 and not in the comparison zones
  is still mixed in. Examples are office return rules and subway service.
- The comparison zones may be touched by the fee over a longer time. Drivers and
  riders have had a year to adjust. That can push the estimate either way.
- The plan's reports are not changed by this record. The new numbers go into their
  own report file.
