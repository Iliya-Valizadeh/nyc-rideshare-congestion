-- The two platforms in scope (ADR 0003). Seed rows come from the TLC data dictionary.
CREATE TABLE IF NOT EXISTS dim_platform (
    platform_key  TINYINT PRIMARY KEY,
    license_num   VARCHAR NOT NULL UNIQUE CHECK (license_num IN ('HV0003', 'HV0005')),
    platform_name VARCHAR NOT NULL UNIQUE
);

INSERT OR IGNORE INTO dim_platform VALUES (1, 'HV0003', 'Uber'), (2, 'HV0005', 'Lyft');
