-- One row per calendar day, from the earliest date in any source to the end of the current
-- Kuala Lumpur month, so every fact date has a match, including the current partial month.
-- generate_series is Postgres (and DuckDB) SQL; see DECISIONS.md #32.
with bounds as (
    select
        least(
            (select min(price_date) from {{ ref('stg_fuel_prices') }}),
            (select min(month_date) from {{ ref('stg_cpi_state') }}),
            (select min(rate_date)  from {{ ref('stg_fx_rates') }})
        ) as start_date,
        greatest(
            (select max(price_date) from {{ ref('stg_fuel_prices') }}),
            (select max(month_date) from {{ ref('stg_cpi_state') }}),
            (select max(rate_date)  from {{ ref('stg_fx_rates') }}),
            cast({{ kl_current_month() }} + interval '1 month' - interval '1 day' as date)
        ) as end_date
),
days as (
    select cast(generate_series(start_date, end_date, interval '1 day') as date) as date_day
    from bounds
)
select
    date_day,
    cast(date_trunc('month', date_day) as date)        as month_date,
    cast(extract(year from date_day) as integer)       as year_number,
    cast(extract(quarter from date_day) as integer)    as quarter_number,
    cast(extract(month from date_day) as integer)      as month_number,
    trim(to_char(date_day, 'Month'))                   as month_name,
    cast(extract(isodow from date_day) as integer)     as iso_day_of_week,
    trim(to_char(date_day, 'Day'))                     as day_name,
    extract(isodow from date_day) in (6, 7)            as is_weekend,
    date_trunc('month', date_day) = {{ kl_current_month() }} as is_current_month
from days
