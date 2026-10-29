# 0001: Data sources, months and download plan

Date: 2026-10-21. Status: accepted.

## Context

The study needs Lyft and Uber trips in New York City from before and after congestion
pricing started on January 5, 2025. The spec asks for pre-trend plots for 2023 to 2024,
a same-months-last-year check, and two study windows: October to December 2024 and
January to March 2025.

This machine has 7 GB of RAM, so the plan is to read one month at a time. No trip
counts, fares or durations were computed for this decision. Only file metadata was read:
sizes, dates and column names and types.

The script `src/nyc_rideshare_congestion/sources/manifest.py` collects every fact in
this file that can change. It sends one HEAD request per file and reads only the
Parquet footer, which holds the schema. Its output is
[`reports/source_manifest.json`](../../reports/source_manifest.json). Run it with
`uv run python -m nyc_rideshare_congestion.sources.manifest`.

## Sources

| What | URL | Checked |
|---|---|---|
| Trip files, one per month | `https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_YYYY-MM.parquet` | HEAD request per file |
| Download page | <https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page> | Read by the researcher agent |
| Taxi zone lookup | `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv` | HEAD request, and fetched once to check boroughs |
| Taxi zone shapes | `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip` | HEAD request |
| Data dictionary | <https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_hvfhs.pdf> | Read in full (version dated March 18, 2025) | <!-- not-a-claim -->
| Zones inside the congestion zone | <https://data.ny.gov/d/yfdc-w5jh> | Zone list read through its API |

The trip files are the TLC (Taxi and Limousine Commission) "High Volume For-Hire
Vehicle" records. In them, each row is one trip. The data dictionary lists `HV0003` as
Uber and `HV0005` as Lyft. Other license numbers are out of scope.

The nyc.gov server refuses plain `curl` requests for the dictionary PDF with "Access
Denied". The web-fetch tool could read it. A later task that needs the PDF again may
have to open it in a browser.

## The congestion fee column

- The column is `cbd_congestion_fee`, of type `double`.
- The dictionary describes it as "Per-trip charge for MTA's Congestion Relief Zone
  starting Jan. 5, 2025."
- The footers show it only in the January to March 2025 files. The 2023 and 2024 files
  do not have it.
- The January 2025 file includes January 1 to 4, before the fee started. The fee column
  is not used to say which trips are in the zone (see below).

## Schema differences between months

The manifest found three schemas.

- January 2023 stores text as `string` and the two zone IDs as `int64`.
- February 2023 to December 2024 use `large_string` and `int32`.
- January to March 2025 match the middle group and add `cbd_congestion_fee`.

The column names are otherwise the same in every file. The loader in T05 must cast
each month to one target schema and must accept a missing `cbd_congestion_fee` for
months before 2025.

## Zones inside the congestion zone

The congestion zone is Manhattan south of and including 60th Street. It leaves out the <!-- not-a-claim -->
FDR Drive, the West Side Highway, the Battery Park Underpass and the tunnel links. Taxi
zones do not follow that line exactly. So this project uses the official MTA dataset
"MTA Central Business District Taxi Zones" (data.ny.gov id `yfdc-w5jh`). The dataset
says it exists so that users of TLC trip records can tell which trips start or end in
a taxi zone inside the tolled area.

It lists 38 zones. All of them are in Manhattan in the TLC zone lookup:

```text
4, 12, 13, 45, 48, 50, 68, 79, 87, 88, 90, 100, 107, 113, 114, 125, 137, 144, 148,
158, 161, 162, 163, 164, 170, 186, 209, 211, 224, 229, 230, 231, 232, 233, 234, 246,
249, 261
```

Source query: `https://data.ny.gov/resource/yfdc-w5jh.json?$select=taxi_zone&$order=taxi_zone`.
The dataset metadata says its rows were last updated on 2024-08-16. The list is used
exactly as published. The project does not add or drop zones, and it never infers the
list from the fee column. The three island zones (Governor's, Ellis and Liberty Island)
are south of 60th Street but are not on the list. They stay out. <!-- not-a-claim -->

## Terms of use

- The TLC page gives no license. Its only statement is that "TLC makes no
  representations as to the accuracy of these data." It links to the NYC.gov Terms of
  Use (<https://www.nyc.gov/main/terms-of-use>). Those terms say content comes "as is"
  with no warranty, and that City content is the property of the City of New York.
- The MTA dataset metadata has no license field.
- What this project does: it downloads the files for analysis, keeps them in the
  gitignored `data/raw/`, and publishes only aggregates (tables and charts). It does not
  republish trip rows. It credits TLC and the MTA as the sources.

## Options for the months

- Only the study windows, October 2024 to March 2025. Smallest download, but no
  pre-trend check and no same-months-last-year check. Rejected: the spec needs both.
- January 2023 to March 2025. Covers 2023 and 2024 pre-trends, the same windows a year
  earlier (October to December 2023 against January to March 2024), and the study
  windows. Chosen.
- Add later 2025 months. The TLC page lists files well past March 2025. They would add
  post-period months the spec does not ask for. They are left out unless the
  pre-registered plan (T03) asks for them.

## Decision

Fetch the 27 monthly files from January 2023 to March 2025, plus the zone lookup, the
zone shapes and the MTA zone list.

Sizes from the HEAD requests: the 27 files total 13.05 GB (decimal gigabytes). With
20% headroom the need is 15.66 GB. Free space on drive C when checked was 278.24 GB, so
the disk check passes. The warehouse and DuckDB spill files will need more space on
top. T04 sizes those.

Download order for T05:

1. October 2024 to March 2025 (the study windows).
2. October 2023 to March 2024 (the same months a year earlier).
3. The rest of 2023 and 2024.

T05 downloads one month per command. It records the SHA-256 hash and the
`Last-Modified` header of each file in the load log. A later re-run can then see if
TLC has published a file again. The TLC page warns that the Parquet schema may still
change "to standardize the parquet schema across all years and datasets".

## Consequences

- The loader must handle the three schemas above.
- Free space must be checked again right before T05. It is a snapshot, not a promise.
- The zone list depends on one MTA dataset from 2024. If MTA changes it, a new ADR
  must say so.
- The 2025 fee column can be used to check the zone list after T03, never as its
  source.
