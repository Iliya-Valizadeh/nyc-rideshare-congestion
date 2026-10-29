# Glossary

Each technical term has a heading and one plain sentence. Link a term to its heading
the first time it appears in each file. `make check-docs` checks this.

## Baseline

A simple method, such as always guessing the most common answer, that a model must
beat to be worth using.

## Bootstrap

A way to see how much a number could change: draw many new samples from the test rows,
with repeats allowed, and compute the number again on each one.

## Confidence interval

A range around a measured number that shows how much the number could move if the
test were run again on new data.

## Mean absolute error

The average size of a miss, in the same units as the thing being predicted. Lower is
better.

## Cluster bootstrap

A bootstrap that draws whole groups, such as whole zones with all their days, instead
of single rows, so that rows which move together stay together.

## Difference-in-differences

A way to estimate the effect of a change: take how much the affected group moved from
before to after, and subtract how much a similar unaffected group moved over the same
time.

## Event study

A chart of the gap between the affected group and the comparison group, period by
period, before and after a change, so a reader can see when the gap opened.

## Parallel trends

The assumption behind difference-in-differences: without the change, the affected
group and the comparison group would have moved the same way.

## Placebo test

The same analysis run at a time or place where nothing changed, which should find
nothing; a clear result there means the method picks up something other than the
change.

## Primary outcome

The one result, named before any data is seen, that the main conclusion rests on.

## Smallest effect of interest

The smallest effect, chosen before the analysis, that would matter in practice;
smaller effects are treated as too small to act on.

## Spillover

When a change also moves the comparison group, for example riders getting dropped
just outside a fee area, which makes the comparison less clean.

## Triple difference

A difference-in-differences minus the same estimate from a year with no change, which
removes a seasonal gap between the two groups.

## Idempotent

A step you can run twice with the same result as running it once, so a stopped run
can simply be started again.
