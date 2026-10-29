-- Monthly average exchange rate in RINGGIT PER ONE UNIT of foreign currency.
-- BNM quotes JPY per 100 units; dividing by quote_unit converts every currency to the same basis.
-- Uses the mid rate, the average of buying and selling, and a single BNM session so that
-- two sessions of the same day are never averaged together.
-- Grain: one row per (currency_code, month_date).
select
    currency_code,
    cast(date_trunc('month', rate_date) as date)                       as month_date,
    cast(avg(middle_rate / quote_unit) as numeric(14, 6))               as rate_myr_per_unit,
    count(middle_rate)                                                  as trading_days
from {{ ref('stg_fx_rates') }}
where session_code = '{{ var("fx_session", "1700") }}'
  and middle_rate is not null
group by 1, 2
