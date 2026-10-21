# Evaluation plan

This is the study plan for Part 2. It was written and committed before any trip
count, fare or duration was computed by zone or by month. The commit that adds this
file is the proof that the plan came first. After results exist, this file is never
edited. Any change goes into a new ADR, as the last section explains.

## In plain words

On January 5, 2025, New York started charging a fee on Uber and Lyft trips into the
south part of Manhattan. We want to know if fewer people took those trips. We compare
trips into the fee area with trips into nearby areas that have no fee. We do this for
the three months before and the three months after the start. If both groups were
moving the same way before, the gap that opens after the start points to the fee. We
also run the same test one year earlier, when there was no fee. That test should find
nothing. This file fixes every choice now, so we cannot pick the choices that give a
nicer answer later.

## Question

Did congestion pricing change the number of Uber and Lyft trips into the congestion
relief zone, compared with similar trips into other Manhattan and Brooklyn zones?
Fare, duration and the split between Lyft and Uber are secondary questions.

## Data

- Source: TLC High Volume For-Hire Vehicle trip files, one per month, from January
  2023 to March 2025 ([ADR 0001](decisions/0001-data-sources.md)).
- Platforms: `HV0003` (Uber) and `HV0005` (Lyft). All other license numbers are left
  out.
- Rows come from the warehouse fact table after the pipeline's checks. Quarantined
  rows are never used.
- A trip's day is the calendar date of `pickup_datetime`, in the local time the TLC
  files use.

## Groups of zones

All zone lists are in the parameter block at the end of this file. The analysis code
reads that block, so the code and the plan cannot drift apart.

- Treated zones: the zones on the MTA list from ADR 0001, used exactly as published.
  The fee column is never used to decide which zones are treated.
- Buffer zones: Manhattan zones outside the list that share a border with a listed
  zone, in the TLC zone shapes file (`taxi_zones.zip`). They are Central Park, Lenox
  Hill East, Lenox Hill West, Lincoln Square East, Lincoln Square West and Upper East
  Side South. They are left out because of [spillover](glossary.md#spillover): a rider
  can get dropped just north of the zone and walk in. That would raise trips there
  because of the fee.
- Island zones: Governor's, Ellis and Liberty Island are south of 60th Street <!-- not-a-claim -->
  but are not on the MTA list (ADR 0001). They are left out of both groups.
- Control zones: every other Manhattan zone, and every Brooklyn zone. Brooklyn is
  across the East River, so no Brooklyn zone shares a border with a treated zone.
- The code that builds the panel must work out the buffer zones from the shapes file
  again. It fails if its list differs from the parameter block.

## Which trips count

The study looks at trips into the zone. So both groups use only trips whose pickup is
outside the treated zones.

- Treated trips: pickup outside the treated zones, drop-off in a treated zone.
- Control trips: pickup outside the treated zones, drop-off in a control zone.
- Trips that start inside the treated zones are left out of both groups. They pay the
  fee whatever their drop-off, so they cannot serve as controls.
- Trips with a pickup in Newark Airport, "Unknown" or "Outside of NYC" (zones `1`,
  `264` and `265`) are left out of both groups. Their route is unknown, and trips from
  New Jersey often enter Manhattan through the zone.
- Some control trips may drive through the zone and pay the fee on the way. To limit
  this, a control trip is left out when one end is in Brooklyn or Staten Island and
  the other end is in Manhattan outside the treated zones or in the Bronx. Queens
  trips stay in.
- After the fee starts, the share of kept control trips with `cbd_congestion_fee > 0`
  is reported. It measures how much the control group was touched by the fee anyway.

## Unit of analysis

One cell is one drop-off zone, on one day, for one platform. Each cell holds the trip
count and the sums of base fare, rider price, trip time and trip miles. Zones with no
trips on a day get a cell with zero trips. Every estimate below is built by adding up
cells, so large zones weigh more. That matches the question, which is about all trips
into the zone, not the average zone.

## Time windows

- Before: 2024-10-01 to 2024-12-31.
- After: 2025-01-05 to 2025-03-31.
- The days 2025-01-01 to 2025-01-04 are in the January file but come before the fee.
  They are left out of both windows.
- Holidays change trips into an office district more than trips into homes. So these
  days are left out of the main estimate: Indigenous Peoples' Day (2024-10-14),
  Veterans Day (2024-11-11), Thanksgiving and the days on each side (2024-11-27 to
  2024-11-29), the end-of-year holidays (2024-12-24 to 2025-01-04), Martin Luther King
  Jr. Day (2025-01-20) and Presidents' Day (2025-02-17). A secondary estimate keeps
  them in.
- The check one year earlier uses the same windows shifted back one year. Its holidays
  are the same holidays in that year (the dates are in the parameter block).

## Outcomes

Each outcome comes with what else could move it, besides the fee. The control group
removes changes that hit both groups the same way. It does not remove changes that hit
the zone more than the control zones. Those are listed below.

### Trip volume (primary outcome)

The [primary outcome](glossary.md#primary-outcome) is the number of Uber and Lyft
trips into the zone per day, both platforms together. It was named before any result.

What else could move it:

- Office return rules that start at the new year would add trips into the zone. That
  would push the estimate toward zero.
- Tourism and holiday shopping in December lift trips into Midtown more than trips into
  homes. The holiday rules and the check one year earlier target this.
- Snow and cold days hurt walking and transit. They can raise or lower rideshare trips
  in different ways for the two groups.
- Subway service changes, and street events such as parades or road closures.
- Riders who switch to yellow taxis, which are not in this data.
- Drivers who leave the zone and serve the control zones instead. Extra supply there
  could raise control trips and make the fall in the zone look larger.

### Fare per trip (secondary)

Two fare measures are reported, in dollars per trip, before tips.

- Base fare: `base_passenger_fare`. This is the platform's own price. It leaves out
  the fee, tolls, taxes and surcharges.
- Rider price: `base_passenger_fare + tolls + bcf + sales_tax + congestion_surcharge +
  airport_fee + cbd_congestion_fee`. The fee column does not exist before 2025, so it
  counts as zero then.

How the fee enters: it is a separate line, so it raises the rider price by itself. The
base fare shows if the platforms also changed their own prices in the zone. The column
`congestion_surcharge` is an older state charge that already existed before 2025. It is
not the new fee. The average `cbd_congestion_fee` on treated trips after the start is
reported as a fact, not estimated.

What else could move it: trip length (shorter trips cost less), platform price changes
at the start of a year, surge pricing that follows demand, changes to TLC driver pay
rules, and toll changes.

### Duration (secondary)

Trip time from `trip_time`, in minutes per trip. Minutes per mile is reported next to
it, because it separates slower traffic from shorter trips.

What else could move it: the fee's own goal of lighter traffic, trip length, weather,
road works, and holiday traffic in December. Holiday traffic would make the before
window slower, so it could make an improvement look larger.

### Lyft share (secondary)

Lyft trips divided by Uber plus Lyft trips, in percentage points.

What else could move it: promotions by one platform, one platform covering the fee for
riders, a shift in where each platform's drivers wait, and a difference in how each
platform reports the fee column.

### Per-platform volume (secondary)

Trip volume for Uber alone and for Lyft alone, measured the same way as the primary
outcome.

## Estimator

### Main estimate

The main estimate is a 2x2 [difference-in-differences](glossary.md#difference-in-differences).
For trip volume it is a ratio of ratios, where each term is mean trips per day over the
days kept in that window.

```text
effect = (treated_after / treated_before) / (control_after / control_before) - 1
```

It reads as the percent change in trips into the zone, beyond the change in the control
zones. For fares and duration, the same formula is used with differences instead of
ratios, on trip-weighted means (dollars and minutes). For the Lyft share it is a
difference in percentage points.

### Event study

An [event study](glossary.md#event-study) shows the same comparison week by week. For
each week from October 2024 to March 2025, take the log of treated trips over control
trips, and subtract its mean over the before window. The chart shows each week with a
band from the same [bootstrap](glossary.md#bootstrap) as below. The same line for one year earlier sits beside
it. This chart is for reading the pattern. It is not a second test.

### Pre-trend check

The method needs [parallel trends](glossary.md#parallel-trends): without the fee, the
two groups would have moved the same way. This cannot be seen directly, so it is tested
on the two years before.

- Series: for each month from January 2023 to December 2024, the log of treated trips
  over control trips (mean per day, all days kept).
- Model: that series against a month-of-year effect plus a straight-line trend, fit by
  least squares. The month-of-year effect removes the yearly cycle, so the slope is
  driven by the change from 2023 to 2024.
- Drift: the slope times three months, which is about the gap between the middle of
  the before window and the middle of the after window.
- The check fails if both are true: the interval for the slope (from the bootstrap
  below) leaves out zero, and the drift is at least `max_drift` in the parameter block,
  in log points. That line is half the
  [smallest effect of interest](glossary.md#smallest-effect-of-interest), because a drift
  that size would already hide half of an effect that matters.
- A secondary estimate takes the drift out of the main estimate. It is always
  reported.

### Check one year earlier

This is a [placebo test](glossary.md#placebo-test). It runs the main estimate on
2023-10-01 to 2023-12-31 against 2024-01-05 to 2024-03-31, with the same zones and
rules. There was no fee then, so the answer should be close to zero. A result far from
zero means the season change from fall to winter differs between the groups. Then the
main estimate mixes the fee with the season.

- The check fails if both are true: the interval leaves out zero, and the placebo
  effect is at least as large as the smallest effect of interest.
- If it fails, the memo leads with the [triple difference](glossary.md#triple-difference):
  the main estimate minus the placebo estimate, on the log scale. It is always
  reported either way.

Each secondary outcome also gets both checks, with the same rules. They decide how
strongly that outcome may be worded.

### Smallest effect that matters

The smallest effect of interest for trip volume is a change of `3%` either way. This
is a judgment made now, not a number taken from the data. A smaller change would not
change how a platform positions drivers or prices trips in the zone. It is used to read
the placebo test and a null result. Secondary outcomes have no such line. They are
described with their intervals and nothing more.

## Inference

- Method: a [cluster bootstrap](glossary.md#cluster-bootstrap) by zone. Each round
  draws treated zones with replacement from the treated zones, and control zones with
  replacement from the control zones. A drawn zone brings all its days and both
  platforms. One draw of zones is used for every estimate in that round, so the main
  estimate and the checks share their noise.
- Why zones and not days: trips in one zone on nearby days are linked. Weather comes in
  spells, and each zone has its own weekly rhythm. Drawing single days would treat
  those days as unrelated and give intervals that are too narrow. Drawing whole zones
  keeps that link.
- Rounds: `2000`. Seed: `20250105`, through `numpy.random.default_rng`.
- Interval: the `0.025` and `0.975` quantiles of the bootstrap values, a `95%`
  interval. The point estimate comes from the full data, not the bootstrap mean.
- Second interval: a bootstrap that draws whole calendar weeks (Monday to Sunday)
  within each window, keeping all zones. It catches shocks that hit every zone on the
  same days. It is reported next to the zone interval. If the two disagree on whether
  zero is inside, the memo uses the wider one.
- Only the primary outcome can support a headline. Every other result is labelled
  secondary. The memo makes no claims that rest on testing many outcomes, and no
  multiple-testing correction is applied, because no secondary result is used as a
  finding on its own.

## Data-quality rules

### Stop rules

If any of these fails, no outcome is reported. The cause is written in an ADR. A
pipeline bug may be fixed and the same rule applied again. A problem in the TLC data
itself needs a deviation ADR before the study goes on.

- Any month from January 2023 to March 2025 fails to cast to the target schema.
- In any month, quarantined rows are more than `max_quarantine_share` of Uber and Lyft
  rows.
- In any month of the study or placebo windows, the quarantine share for trips ending
  in treated zones and for trips ending in control zones differs by more than
  `max_quarantine_gap`.
- The share of Uber and Lyft trips with an unknown drop-off zone (`264` or `265`)
  changes between the before and after windows by more than `max_unknown_dropoff_change`.
- Any day in the study or placebo windows has zero rows for either platform.

### Flags

These do not stop the study. They are reported and they change the memo's wording.

- After the start, under `min_treated_fee_share` of treated trips carry a fee above
  zero. Then the memo says the estimate is for trips into the listed zones, and gives
  the share that paid.
- After the start, over `max_control_fee_share` of control trips carry a fee above
  zero. Then the memo says the control group was partly touched by the fee, which
  pushes the estimate toward zero.

### Pipeline checks before analysis

- All months are loaded, each with one load-log row holding its SHA-256 hash.
- Running the load again adds zero rows.
- For each month, kept rows plus quarantined rows equal the Uber and Lyft rows in the
  file.
- No trip appears twice in the fact table.
- Every zone key in the fact table exists in the zone table.
- The freshness and row-count check passes.
- The panel's trip total for each month equals the fact table's total for the same
  rows.

## What the memo may say

| Result | What the memo may say |
|---|---|
| Interval leaves out zero, both checks pass, no stop | "The data point to congestion pricing changing trips into the zone by X% (interval a to b), against other Manhattan and Brooklyn zones." Never "proves". |
| Interval leaves out zero, pre-trend check fails | Describe only: "Trips into the zone changed X% more than trips into other zones. The zone was already on a different path before the fee, so this does not isolate the fee." Report the drift-adjusted estimate as secondary. |
| Interval leaves out zero, placebo check fails | Lead with the triple difference, with "is consistent with" wording. Say the plain estimate mixes the fee with the season. |
| Interval holds zero and lies inside the smallest effect of interest | "We find no change larger than 3% in either direction." Never "no effect". <!-- not-a-claim --> |
| Interval holds zero and is wider than that | "The result is not clear. The data cannot rule out a change of up to the interval's ends." |
| A stop rule fails | No estimates. The memo reports the stop and its cause. |

In every case:

- The recommendation follows from the primary result and its checks.
- Secondary outcomes may give context. They are described with intervals and never
  called findings.
- Both checks are reported, pass or fail, with their numbers.
- Analyses not listed here are labelled exploratory.

## Changes to this plan

This file is not edited after results exist. A change is a deviation. It goes into a
new ADR named `NNNN-deviation-*.md` that says what changed and why, and it is listed in
`whats_weak.md`. None yet.

## Parameter block

The analysis code reads this block. Dates are inclusive.

```json
{
  "platforms": {"HV0003": "Uber", "HV0005": "Lyft"},
  "treated_zones": [4, 12, 13, 45, 48, 50, 68, 79, 87, 88, 90, 100, 107, 113, 114, 125,
                    137, 144, 148, 158, 161, 162, 163, 164, 170, 186, 209, 211, 224, 229,
                    230, 231, 232, 233, 234, 246, 249, 261],
  "buffer_zones": [43, 140, 141, 142, 143, 237],
  "island_zones": [103, 104, 105],
  "excluded_pickup_zones": [1, 264, 265],
  "control_zones_manhattan": [24, 41, 42, 74, 75, 116, 120, 127, 128, 151, 152, 153, 166,
                              194, 202, 236, 238, 239, 243, 244, 262, 263],
  "control_zones_brooklyn": [11, 14, 17, 21, 22, 25, 26, 29, 33, 34, 35, 36, 37, 39, 40,
                             49, 52, 54, 55, 61, 62, 63, 65, 66, 67, 71, 72, 76, 77, 80,
                             85, 89, 91, 97, 106, 108, 111, 112, 123, 133, 149, 150, 154,
                             155, 165, 177, 178, 181, 188, 189, 190, 195, 210, 217, 222,
                             225, 227, 228, 255, 256, 257],
  "cross_zone_rule": {
    "far_side_a": ["Brooklyn", "Staten Island"],
    "far_side_b": ["Manhattan outside treated zones", "Bronx"],
    "drop_control_trip_if_ends_on_opposite_sides": true
  },
  "windows": {
    "before": ["2024-10-01", "2024-12-31"],
    "after": ["2025-01-05", "2025-03-31"],
    "placebo_before": ["2023-10-01", "2023-12-31"],
    "placebo_after": ["2024-01-05", "2024-03-31"],
    "pretrend_months": ["2023-01", "2024-12"]
  },
  "excluded_dates": {
    "study": ["2024-10-14", "2024-11-11", "2024-11-27", "2024-11-28", "2024-11-29",
              "2024-12-24/2025-01-04", "2025-01-20", "2025-02-17"],
    "placebo": ["2023-10-09", "2023-11-10", "2023-11-22", "2023-11-23", "2023-11-24",
                "2023-12-24/2024-01-04", "2024-01-15", "2024-02-19"]
  },
  "primary_outcome": "trips_per_day_both_platforms",
  "smallest_effect_of_interest": 0.03,
  "bootstrap": {
    "cluster": "zone",
    "strata": ["treated", "control"],
    "rounds": 2000,
    "seed": 20250105,
    "interval_level": 0.95,
    "second_interval": "calendar_week_blocks_within_window"
  },
  "pretrend": {"drift_months": 3, "max_drift": 0.015},
  "placebo": {"max_effect": "smallest_effect_of_interest"},
  "stop_rules": {
    "max_quarantine_share": 0.02,
    "max_quarantine_gap": 0.005,
    "max_unknown_dropoff_change": 0.01,
    "min_rows_per_platform_per_day": 1
  },
  "flags": {"min_treated_fee_share": 0.90, "max_control_fee_share": 0.05}
}
```
