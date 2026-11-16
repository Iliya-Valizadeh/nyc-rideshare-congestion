-- Kept trips for one month that the plan counts ("Which trips count"), summed to the
-- panel grain. Parameters: $lo and $hi (date_key range), $treated, $control,
-- $excluded_pickup (zone lists), $side_a and $side_b (borough lists for the rule that
-- drops control trips which may drive through the zone).
SELECT
    f.date_key,
    f.platform_key,
    f.do_zone_key,
    sum(f.trip_count) AS trips,
    sum(f.base_fare_sum) AS base_fare_sum,
    sum(f.rider_price_sum) AS rider_price_sum,
    sum(f.trip_time_sum_s) AS trip_time_sum_s,
    sum(f.trip_miles_sum) AS trip_miles_sum,
    sum(f.cbd_fee_sum) AS cbd_fee_sum,
    sum(f.cbd_fee_trip_count) AS cbd_fee_trips
FROM fact_trip_hourly AS f
JOIN dim_zone AS pu ON pu.zone_key = f.pu_zone_key
JOIN dim_zone AS dz ON dz.zone_key = f.do_zone_key
WHERE f.date_key BETWEEN $lo AND $hi
    AND NOT list_contains($treated, f.pu_zone_key)
    AND NOT list_contains($excluded_pickup, f.pu_zone_key)
    AND (
        list_contains($treated, f.do_zone_key)
        OR (
            list_contains($control, f.do_zone_key)
            AND NOT (list_contains($side_a, pu.borough) AND list_contains($side_b, dz.borough))
            AND NOT (list_contains($side_b, pu.borough) AND list_contains($side_a, dz.borough))
        )
    )
GROUP BY 1, 2, 3
