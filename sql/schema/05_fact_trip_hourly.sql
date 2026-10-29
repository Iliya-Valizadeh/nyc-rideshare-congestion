-- Kept trips summed to one row per pickup date, hour, platform, pickup and drop-off zone
-- (ADR 0003). No FOREIGN KEY clauses: the load and the check command test the
-- references instead, because DuckDB foreign keys block the backfill deletes.
CREATE TABLE IF NOT EXISTS fact_trip_hourly (
    date_key           INTEGER NOT NULL,
    pickup_hour        TINYINT NOT NULL CHECK (pickup_hour BETWEEN 0 AND 23),
    platform_key       TINYINT NOT NULL,
    pu_zone_key        SMALLINT NOT NULL,
    do_zone_key        SMALLINT NOT NULL,
    trip_count         INTEGER NOT NULL CHECK (trip_count > 0),
    trip_miles_sum     DOUBLE NOT NULL CHECK (trip_miles_sum >= 0),
    trip_time_sum_s    BIGINT NOT NULL CHECK (trip_time_sum_s > 0),
    base_fare_sum      DOUBLE NOT NULL CHECK (base_fare_sum >= 0),
    rider_price_sum    DOUBLE NOT NULL CHECK (rider_price_sum >= 0),
    cbd_fee_sum        DOUBLE NOT NULL CHECK (cbd_fee_sum >= 0),
    cbd_fee_trip_count INTEGER NOT NULL CHECK (cbd_fee_trip_count BETWEEN 0 AND trip_count),
    load_id            INTEGER NOT NULL,
    PRIMARY KEY (date_key, pickup_hour, platform_key, pu_zone_key, do_zone_key)
);
