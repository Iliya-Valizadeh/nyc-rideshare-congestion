-- Hourly demand by pickup borough (ADR 0007). Grain: one row per date, hour and pickup
-- borough, from the first to the last loaded day. Hours with no trips appear as zero, so
-- the window averages count quiet hours instead of skipping them.
CREATE OR REPLACE TABLE tf_hourly_demand AS
WITH loaded_days AS (
    SELECT d.date_key, d.full_date
    FROM dim_date AS d
    WHERE d.date_key BETWEEN (SELECT min(date_key) FROM fact_trip_hourly)
                         AND (SELECT max(date_key) FROM fact_trip_hourly)
),
boroughs AS (
    SELECT DISTINCT borough FROM dim_zone
),
spine AS (
    SELECT ld.date_key, ld.full_date, h.pickup_hour, b.borough
    FROM loaded_days AS ld
    CROSS JOIN range(24) AS h(pickup_hour)
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
    -- The same hour over the last seven days, this day included.
    avg(trips) OVER (
        PARTITION BY borough, pickup_hour
        ORDER BY full_date
        RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW
    ) AS trips_same_hour_7d_avg,
    -- The same hour one week earlier. Null for the first week.
    lag(trips, 7) OVER (PARTITION BY borough, pickup_hour ORDER BY full_date)
        AS trips_same_hour_last_week,
    trips / nullif(sum(trips) OVER (PARTITION BY date_key, borough), 0) AS share_of_day,
    rank() OVER (PARTITION BY date_key, borough ORDER BY trips DESC) AS hour_rank_in_day
FROM filled;
