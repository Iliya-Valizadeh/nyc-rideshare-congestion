-- Fill one month of the panel: every (day, platform, zone) cell, with zeros where the
-- trips query has no row. Same parameters as 02_panel_trips.sql plus $month_key.
INSERT INTO panel_zone_day
WITH zones AS (
    SELECT unnest($treated) AS do_zone_key, 'treated' AS zone_group
    UNION ALL
    SELECT unnest($control), 'control'
),
cells AS (
    SELECT d.date_key, d.full_date, p.platform_key, z.do_zone_key, z.zone_group
    FROM dim_date AS d CROSS JOIN dim_platform AS p CROSS JOIN zones AS z
    WHERE d.month_key = $month_key
),
trips AS ({trips_sql})
SELECT
    c.date_key,
    c.full_date,
    c.platform_key,
    c.do_zone_key,
    c.zone_group,
    coalesce(t.trips, 0),
    coalesce(t.base_fare_sum, 0),
    coalesce(t.rider_price_sum, 0),
    coalesce(t.trip_time_sum_s, 0),
    coalesce(t.trip_miles_sum, 0),
    coalesce(t.cbd_fee_sum, 0),
    coalesce(t.cbd_fee_trips, 0)
FROM cells AS c
LEFT JOIN trips AS t USING (date_key, platform_key, do_zone_key)
