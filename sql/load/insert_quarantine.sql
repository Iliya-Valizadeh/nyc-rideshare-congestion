-- Failed rows from staging into quarantine_trip. Parameters: load_id, source_month.
INSERT INTO quarantine_trip
SELECT
    ?, src_row, ?, reason_code, reason_detail,
    hvfhs_license_num, dispatching_base_num, originating_base_num,
    request_datetime, on_scene_datetime, pickup_datetime, dropoff_datetime,
    PULocationID, DOLocationID, trip_miles, trip_time, base_passenger_fare,
    tolls, bcf, sales_tax, congestion_surcharge, airport_fee, tips, driver_pay,
    shared_request_flag, shared_match_flag, access_a_ride_flag, wav_request_flag,
    wav_match_flag, cbd_congestion_fee
FROM stg_trip
WHERE reason_code IS NOT NULL;
