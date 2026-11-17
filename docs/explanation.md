# Explanation

Background and reasons. This page says why the project works the way it does. The
tutorial and how-to guides say what to type.

## Why this approach

The question is whether the congestion toll changed rideshare trips into the toll
zone. No one can see what would have happened without the toll. So the study compares
the change in the toll zone with the change in other zones over the same days. That is
[difference-in-differences](glossary.md#difference-in-differences). It works only if
both groups would have moved together, which is [parallel trends](glossary.md#parallel-trends).
The plan in [the evaluation plan](eval_plan.md) fixes the outcome, the zone groups and
the stop rules before the results were read.

The pipeline exists so that this comparison rests on checked data. Bad rows are
quarantined with a reason, and the stop rules end the run if the data is not fit to
use.

## Choices and their costs

Each choice has a record in [decisions](decisions/). The data sources and months are
in ADR 0001, the engine in ADR 0002 and the data model in ADR 0003. Loads and data
quality are in ADR 0004, and how the code reads the plan is in ADR 0008.

The first comparison was confounded by the season. ADR 0009 changes the plan to a
longer window, set against the same months of the year before. The cost is that the
first result is kept as it was and the new one is reported next to it as an amendment.
