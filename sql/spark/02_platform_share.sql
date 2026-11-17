-- Spark SQL copy of sql/transforms/02_platform_share.sql, for the benchmark only.
-- The query is the same text; only the CREATE TABLE line is gone.
WITH monthly AS (
    SELECT d.month_key, z.borough, f.platform_key, sum(f.trip_count) AS trips
    FROM fact_trip_hourly AS f
    JOIN dim_date AS d ON d.date_key = f.date_key
    JOIN dim_zone AS z ON z.zone_key = f.pu_zone_key
    GROUP BY 1, 2, 3
),
cells AS (
    SELECT DISTINCT month_key, borough FROM monthly
),
filled AS (
    SELECT c.month_key, c.borough, p.platform_key, p.platform_name,
           coalesce(m.trips, 0) AS trips
    FROM cells AS c
    CROSS JOIN dim_platform AS p
    LEFT JOIN monthly AS m
        ON m.month_key = c.month_key
        AND m.borough = c.borough
        AND m.platform_key = p.platform_key
),
shares AS (
    SELECT
        *,
        trips / sum(trips) OVER (PARTITION BY month_key, borough) AS trip_share
    FROM filled
)
SELECT
    month_key,
    borough,
    platform_key,
    platform_name,
    CAST(trips AS BIGINT) AS trips,
    trip_share,
    trip_share - lag(trip_share) OVER (
        PARTITION BY borough, platform_key ORDER BY month_key
    ) AS share_change_vs_prev_month
FROM shares
