{{
  config(
    materialized = 'incremental',
    unique_key = ['price_date', 'fuel_type'],
    on_schema_change = 'append_new_columns'
  )
}}
-- One row per weekly price announcement per fuel type.
--
-- Incremental: after the first build, a run only reprocesses the last
-- `fuel_lookback_days` (default 21) of announcements and replaces those keys
-- (dbt-postgres' delete+insert). Same reasoning as the ingestion lookback (DECISIONS.md #3):
-- a "newer than the max date" filter would never pick up a revised price.
-- Revisions older than the window need `--full-refresh`, or the snapshot (step 3b).
--
-- The weekly change comes from a view that computes LAG over the FULL history, and only then is
-- the window applied, so the first row inside the window still compares with the row before it.
select
    {{ dbt_utils.generate_surrogate_key(['price_date', 'fuel_type']) }} as fuel_price_key,
    price_date,
    fuel_type,
    price_myr,
    previous_price_myr,
    change_myr,
    days_since_previous,
    coalesce(change_myr <> 0, false)                                    as is_price_change
from {{ ref('int_fuel_weekly_changes') }}
{% if is_incremental() %}
where price_date >= (
    select max(price_date) - {{ var('fuel_lookback_days', 21) }}
    from {{ this }}
)
{% endif %}
