-- Kept rows from staging, summed to the fact grain (ADR 0003). Parameter: load_id.
-- A null fee part counts as zero in the rider price (ADR 0004).
INSERT INTO fact_trip_hourly
SELECT
    year(s.pickup_datetime) * 10000 + month(s.pickup_datetime) * 100 + day(s.pickup_datetime),
    hour(s.pickup_datetime),
    p.platform_key,
    s.PULocationID,
    s.DOLocationID,
    count(*),
    sum(s.trip_miles),
    sum(s.trip_time),
    sum(s.base_passenger_fare),
    sum(
        s.base_passenger_fare + coalesce(s.tolls, 0) + coalesce(s.bcf, 0)
        + coalesce(s.sales_tax, 0) + coalesce(s.congestion_surcharge, 0)
        + coalesce(s.airport_fee, 0) + coalesce(s.cbd_congestion_fee, 0)
    ),
    sum(coalesce(s.cbd_congestion_fee, 0)),
    count(*) FILTER (WHERE s.cbd_congestion_fee > 0),
    ?
FROM stg_trip AS s
JOIN dim_platform AS p ON p.license_num = s.hvfhs_license_num
WHERE s.reason_code IS NULL
GROUP BY 1, 2, 3, 4, 5;
