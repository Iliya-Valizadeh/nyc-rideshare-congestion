-- The one index timed in ADR 0003 and ADR 0007: drop-off zone on the fact table.
CREATE INDEX IF NOT EXISTS ix_fact_do_zone ON fact_trip_hourly (do_zone_key);
