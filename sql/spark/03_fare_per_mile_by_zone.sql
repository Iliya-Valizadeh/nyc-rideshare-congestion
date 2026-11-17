-- Spark SQL copy of sql/transforms/03_fare_per_mile_by_zone.sql, for the benchmark only.
-- The query is the same text; only the CREATE TABLE line is gone.
WITH monthly AS (
    SELECT
        d.month_key,
        d.year * 12 + d.month AS month_index,
        f.pu_zone_key,
        f.platform_key,
        sum(f.trip_count) AS trips,
        sum(f.base_fare_sum) AS base_fare,
        sum(f.trip_miles_sum) AS miles
    FROM fact_trip_hourly AS f
    JOIN dim_date AS d ON d.date_key = f.date_key
    GROUP BY 1, 2, 3, 4
),
rolling AS (
    SELECT
        *,
        sum(base_fare) OVER w3 AS base_fare_3m,
        sum(miles) OVER w3 AS miles_3m
    FROM monthly
    WINDOW w3 AS (
        PARTITION BY pu_zone_key, platform_key
        ORDER BY month_index
        RANGE BETWEEN 2 PRECEDING AND CURRENT ROW
    )
)
SELECT
    r.month_key,
    r.pu_zone_key,
    z.borough,
    z.zone_name,
    r.platform_key,
    CAST(r.trips AS BIGINT) AS trips,
    r.base_fare,
    r.miles,
    r.base_fare / nullif(r.miles, 0) AS fare_per_mile,
    r.base_fare_3m / nullif(r.miles_3m, 0) AS fare_per_mile_3m,
    CASE WHEN r.miles > 0 THEN rank() OVER (
        PARTITION BY r.month_key, z.borough, r.platform_key, r.miles > 0
        ORDER BY r.base_fare / nullif(r.miles, 0) DESC
    ) END AS rank_in_borough
FROM rolling AS r
JOIN dim_zone AS z ON z.zone_key = r.pu_zone_key
