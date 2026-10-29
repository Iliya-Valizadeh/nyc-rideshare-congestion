# Datasheet: nyc-rideshare-congestion

Short form of the datasheet from Gebru et al., "Datasheets for Datasets"
(https://arxiv.org/abs/1803.09010). Every number here needs a row in
[CLAIMS.md](CLAIMS.md).

Status: the pipeline sections are filled in. All 27 months are downloaded and loaded.
The study sections wait for the Part 2 analysis.

## Motivation

The dataset is a local warehouse of Lyft and Uber trips in New York City. Iliya
Valizadeh builds it for a portfolio project. It has two aims: show a tested data
pipeline, and study whether congestion pricing (started January 5, 2025) changed Lyft
and Uber trips into the congestion zone.

## Composition

- The source is the TLC (Taxi and Limousine Commission) High Volume For-Hire Vehicle
  trip records. In the source, one row is one trip.
- Only two license numbers are kept: `HV0003` (Uber) and `HV0005` (Lyft), as named in
  the TLC data dictionary.
- Each source row has the license and base numbers, request, arrival, pickup and drop-off
  times, pickup and drop-off taxi zone IDs, miles, seconds, fare parts, driver pay and
  shared-ride and wheelchair flags. From January 2025 it also has
  `cbd_congestion_fee`, the per-trip congestion charge.
- The column types differ slightly between months. See
  [ADR 0001](docs/decisions/0001-data-sources.md).
- The warehouse is a star schema in one DuckDB file. The fact table,
  `fact_trip_hourly`, holds one row per pickup date, pickup hour, platform, pickup zone
  and drop-off zone, with trip counts and sums of miles, time and fares. Its
  dimensions are `dim_date`, `dim_zone` and `dim_platform`. `load_log` holds one row
  per file and `quarantine_trip` holds every rejected row
  ([ADR 0003](docs/decisions/0003-data-model.md)).
- Row counts for every step are in the README table "Where every trip row went", taken
  from `reports/load_summary.json`. Counts for each month are in the same file.

## Collection

- The data comes from the TLC monthly Parquet files at
  `https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_YYYY-MM.parquet`, and
  the TLC taxi zone lookup and shapes.
- The months are January 2023 to March 2025: 27 files, 13.05 GB in total, from HEAD
  requests recorded in `reports/source_manifest.json`.
- The list of taxi zones inside the congestion zone (38 zones) comes from the MTA
  dataset "MTA Central Business District Taxi Zones" (<https://data.ny.gov/d/yfdc-w5jh>).
- Terms: the TLC page gives no license and makes no claim of accuracy. The NYC.gov
  Terms of Use (<https://www.nyc.gov/main/terms-of-use>) apply. The project keeps raw
  files out of git and publishes only aggregates.

## Preprocessing

- `nrc load` checks each row against seven rules in a fixed order (empty required
  column, pickup outside the file month, unknown zone, negative fare, negative distance,
  impossible duration, exact duplicate). A failing row goes to `quarantine_trip` with
  the code of the first rule it breaks. The code is in
  `src/nyc_rideshare_congestion/pipeline/rules.py` and the rules are in
  [ADR 0004](docs/decisions/0004-loads-and-quality.md).
- Rows from license numbers other than `HV0003` and `HV0005` are counted and left out.
- Kept rows are summed to the fact grain. A month loads in one transaction.
- `nrc check` tests freshness, that the counts add up, and that every fact key exists in
  its dimension.

## Uses

- Fits: city-wide and zone-level patterns in trip volume, fares, duration and platform
  share, by hour, day and month.
- Does not fit: anything about single riders or drivers. The records carry no rider or
  driver IDs, and the project does not try to link trips to people.
- Taxi zones only roughly follow the congestion zone line, so zone-level results about
  "in the zone" carry that error. See ADR 0001.

## Distribution and maintenance

The warehouse is not published. Anyone can rebuild it from the TLC files with the
pipeline in this repo. TLC may republish files, so the load log keeps each file's
SHA-256 hash and `Last-Modified` date (T05).
