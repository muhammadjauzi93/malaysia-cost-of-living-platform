-- One row per business day per currency, from one BNM session (var fx_session, default 1700),
-- with every rate in ringgit per ONE unit of foreign currency (JPY divided by 100).
select
    {{ dbt_utils.generate_surrogate_key(['rate_date', 'currency_code']) }} as fx_rate_key,
    rate_date,
    currency_code,
    quote_unit,
    cast(buying_rate  / quote_unit as numeric(14, 6)) as buying_myr_per_unit,
    cast(middle_rate  / quote_unit as numeric(14, 6)) as middle_myr_per_unit,
    cast(selling_rate / quote_unit as numeric(14, 6)) as selling_myr_per_unit
from {{ ref('stg_fx_rates') }}
where session_code = '{{ var("fx_session", "1700") }}'
