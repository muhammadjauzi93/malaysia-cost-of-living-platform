-- Phase 1 schema. Safe to re-run: every statement is IF NOT EXISTS.

CREATE SCHEMA IF NOT EXISTS raw;   -- data as received from sources, lightly typed
CREATE SCHEMA IF NOT EXISTS ops;   -- pipeline metadata, not business data

-- One row per fuel type per weekly price announcement.
-- Grain: (price_date, fuel_type). Long format, see DECISIONS.md #1.
CREATE TABLE IF NOT EXISTS raw.fuel_price_weekly (
    price_date  DATE          NOT NULL,
    fuel_type   TEXT          NOT NULL,
    price_myr   NUMERIC(6, 2) NOT NULL CHECK (price_myr > 0),
    source      TEXT          NOT NULL DEFAULT 'data.gov.my/fuelprice',
    loaded_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (price_date, fuel_type)
);

COMMENT ON TABLE raw.fuel_price_weekly IS
    'Weekly retail fuel prices in MYR per litre. Source: data.gov.my catalogue id fuelprice, series_type = level.';

-- One row per pipeline execution. Written at start, updated at the end.
CREATE TABLE IF NOT EXISTS ops.pipeline_runs (
    run_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pipeline_name   TEXT        NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'success', 'failed')),
    watermark_from  DATE,
    rows_extracted  INTEGER,
    rows_inserted   INTEGER,
    rows_updated    INTEGER,
    landed_file     TEXT,
    error_message   TEXT
);
