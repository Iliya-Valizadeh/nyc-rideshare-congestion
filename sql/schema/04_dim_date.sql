-- One row per calendar day. pipeline/dims.py fills it from the range in config.py.
CREATE TABLE IF NOT EXISTS dim_date (
    date_key        INTEGER PRIMARY KEY,
    full_date       DATE NOT NULL UNIQUE,
    year            SMALLINT NOT NULL,
    month           TINYINT NOT NULL CHECK (month BETWEEN 1 AND 12),
    day             TINYINT NOT NULL CHECK (day BETWEEN 1 AND 31),
    month_key       INTEGER NOT NULL,
    iso_day_of_week TINYINT NOT NULL CHECK (iso_day_of_week BETWEEN 1 AND 7),
    iso_week        TINYINT NOT NULL CHECK (iso_week BETWEEN 1 AND 53),
    is_weekend      BOOLEAN NOT NULL
);
