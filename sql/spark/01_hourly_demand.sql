-- Spark SQL copy of sql/transforms/01_hourly_demand.sql, for the benchmark only.
-- Same grain and columns. Two dialect changes: the hour list comes from range(24) with
-- Spark's column name `id`, and the seven-day window orders by unix_date(full_date),
-- the day number, because a RANGE frame here needs a number to count back from.
WITH loaded_days AS (
    SELECT d.date_key, d.full_date
    FROM dim_date AS d
    WHERE d.date_key BETWEEN (SELECT min(date_key) FROM fact_trip_hourly)
                         AND (SELECT max(date_key) FROM fact_trip_hourly)
),
boroughs AS (
    SELECT DISTINCT borough FROM dim_zone
),
hours AS (
    SELECT CAST(id AS INT) AS pickup_hour FROM range(24)
),
spine AS (
    SELECT ld.date_key, ld.full_date, h.pickup_hour, b.borough
    FROM loaded_days AS ld
    CROSS JOIN hours AS h
    CROSS JOIN boroughs AS b
),
trips AS (
    SELECT f.date_key, f.pickup_hour, z.borough, sum(f.trip_count) AS trips
    FROM fact_trip_hourly AS f
    JOIN dim_zone AS z ON z.zone_key = f.pu_zone_key
    GROUP BY 1, 2, 3
),
filled AS (
    SELECT s.date_key, s.full_date, s.pickup_hour, s.borough, coalesce(t.trips, 0) AS trips
    FROM spine AS s
    LEFT JOIN trips AS t
        ON t.date_key = s.date_key
        AND t.pickup_hour = s.pickup_hour
        AND t.borough = s.borough
)
SELECT
    date_key,
    CAST(pickup_hour AS TINYINT) AS pickup_hour,
    borough,
    CAST(trips AS BIGINT) AS trips,
    avg(trips) OVER (
        PARTITION BY borough, pickup_hour
        ORDER BY unix_date(full_date)
        RANGE BETWEEN 6 PRECEDING AND CURRENT ROW
    ) AS trips_same_hour_7d_avg,
    lag(trips, 7) OVER (PARTITION BY borough, pickup_hour ORDER BY full_date)
        AS trips_same_hour_last_week,
    trips / nullif(sum(trips) OVER (PARTITION BY date_key, borough), 0) AS share_of_day,
    rank() OVER (PARTITION BY date_key, borough ORDER BY trips DESC) AS hour_rank_in_day
FROM filled
