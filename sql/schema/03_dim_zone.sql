-- TLC taxi zones, loaded from taxi_zone_lookup.csv by pipeline/dims.py (ADR 0003).
CREATE TABLE IF NOT EXISTS dim_zone (
    zone_key           SMALLINT PRIMARY KEY CHECK (zone_key > 0),
    borough            VARCHAR NOT NULL,
    zone_name          VARCHAR NOT NULL,
    service_zone       VARCHAR,
    in_congestion_zone BOOLEAN NOT NULL
);
