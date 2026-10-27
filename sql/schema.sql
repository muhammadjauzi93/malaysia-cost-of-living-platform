-- Platform schema (Phases 1-2). Safe to re-run: every statement is IF NOT EXISTS.
-- On an existing database apply it with `python tasks.py init-db`.

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

-- ---------------------------------------------------------------------------------------------
-- Phase 2
-- ---------------------------------------------------------------------------------------------

-- One row per state, month and COICOP division. Source: OpenDOSM dataset cpi_state.
-- Grain: (state, month_date, division). month_date is always the 1st of the month.
CREATE TABLE IF NOT EXISTS raw.cpi_state_monthly (
    state       TEXT          NOT NULL,
    month_date  DATE          NOT NULL,
    division    TEXT          NOT NULL,   -- '01'..'13' or 'overall'
    cpi_index   NUMERIC(10, 3) NOT NULL CHECK (cpi_index > 0),   -- base 2010 = 100
    source      TEXT          NOT NULL DEFAULT 'data.gov.my/cpi_state',
    loaded_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (state, month_date, division)
);

COMMENT ON TABLE raw.cpi_state_monthly IS
    'State-level CPI by division, base 2010 = 100. Source: OpenDOSM dataset cpi_state.';

-- One row per currency per publication date per BNM session. Rates are stored exactly as BNM
-- quotes them: `unit` says how many foreign units the rate is for (JPY is quoted per 100).
-- Normalising to "ringgit per 1 unit" is a derivation, so it belongs in the dbt layer.
CREATE TABLE IF NOT EXISTS raw.fx_rate_daily (
    rate_date     DATE           NOT NULL,
    currency      TEXT           NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    session_code  TEXT           NOT NULL,   -- BNM snapshot: 0900, 1130, 1200 or 1700
    unit          INTEGER        NOT NULL CHECK (unit > 0),
    buying_rate   NUMERIC(14, 4) CHECK (buying_rate > 0),
    selling_rate  NUMERIC(14, 4) CHECK (selling_rate > 0),
    middle_rate   NUMERIC(14, 4) CHECK (middle_rate > 0),
    source        TEXT           NOT NULL DEFAULT 'bnm/exchange-rate',
    loaded_at     TIMESTAMPTZ    NOT NULL DEFAULT now(),
    PRIMARY KEY (rate_date, currency, session_code),
    CHECK (buying_rate IS NOT NULL OR selling_rate IS NOT NULL OR middle_rate IS NOT NULL)
);

COMMENT ON TABLE raw.fx_rate_daily IS
    'MYR exchange rates from Bank Negara Malaysia, per `unit` units of foreign currency.';
