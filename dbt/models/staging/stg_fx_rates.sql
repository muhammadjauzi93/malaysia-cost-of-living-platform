-- One row per currency per date per BNM session.
-- Rates stay exactly as BNM quotes them, i.e. per `quote_unit` units (JPY per 100).
-- Converting to "ringgit per 1 unit" is a derivation, so it happens in the intermediate layer.
select
    rate_date,
    upper(trim(currency))                  as currency_code,
    session_code,
    unit                                   as quote_unit,
    cast(buying_rate as numeric(14, 4))    as buying_rate,
    cast(selling_rate as numeric(14, 4))   as selling_rate,
    cast(middle_rate as numeric(14, 4))    as middle_rate,
    source                                 as source_system,
    loaded_at
from {{ source('raw', 'fx_rate_daily') }}
