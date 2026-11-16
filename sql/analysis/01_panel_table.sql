-- The analysis panel (docs/eval_plan.md, "Unit of analysis"): one row per drop-off zone,
-- day and platform, for treated and control zones only. Days with no trips hold zeros.
CREATE TABLE IF NOT EXISTS panel_zone_day (
    date_key        INTEGER NOT NULL,
    full_date       DATE NOT NULL,
    platform_key    TINYINT NOT NULL,
    do_zone_key     SMALLINT NOT NULL,
    zone_group      VARCHAR NOT NULL CHECK (zone_group IN ('treated', 'control')),
    trips           BIGINT NOT NULL CHECK (trips >= 0),
    base_fare_sum   DOUBLE NOT NULL,
    rider_price_sum DOUBLE NOT NULL,
    trip_time_sum_s BIGINT NOT NULL,
    trip_miles_sum  DOUBLE NOT NULL,
    cbd_fee_sum     DOUBLE NOT NULL,
    cbd_fee_trips   BIGINT NOT NULL,
    PRIMARY KEY (date_key, platform_key, do_zone_key)
);
