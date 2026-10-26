-- One row per (file name, SHA-256) pair. ADR 0004 defines every column.
CREATE SEQUENCE IF NOT EXISTS load_log_id_seq START 1;

CREATE TABLE IF NOT EXISTS load_log (
    load_id             INTEGER PRIMARY KEY DEFAULT nextval('load_log_id_seq'),
    file_name           VARCHAR NOT NULL,
    sha256              VARCHAR NOT NULL CHECK (regexp_full_match(sha256, '[0-9a-f]{64}')),
    source_month        VARCHAR NOT NULL CHECK (regexp_full_match(source_month, '[0-9]{4}-[0-9]{2}')),
    size_bytes          BIGINT NOT NULL CHECK (size_bytes >= 0),
    last_modified       TIMESTAMP,
    status              VARCHAR NOT NULL CHECK (status IN ('downloaded', 'loaded', 'replaced')),
    downloaded_at       TIMESTAMP NOT NULL,
    loaded_at           TIMESTAMP,
    rows_in_file        BIGINT,
    rows_other_platform BIGINT,
    rows_kept           BIGINT,
    rows_quarantined    BIGINT,
    rows_fee_part_null  BIGINT,
    fact_rows           BIGINT,
    extra_columns       VARCHAR[],
    package_version     VARCHAR,
    UNIQUE (file_name, sha256)
);
