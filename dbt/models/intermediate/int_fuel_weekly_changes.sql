-- Week-on-week change for every fuel type, computed from the previous announcement of THAT fuel.
-- LAG runs within each fuel type, so a new fuel type starts with no previous value rather than
-- inheriting another fuel's price.
-- Grain: one row per (price_date, fuel_type), the same as stg_fuel_prices.
with with_previous as (
    select
        price_date,
        fuel_type,
        price_myr,
        lag(price_date) over (partition by fuel_type order by price_date) as previous_price_date,
        lag(price_myr)  over (partition by fuel_type order by price_date) as previous_price_myr
    from {{ ref('stg_fuel_prices') }}
)
select
    price_date,
    fuel_type,
    price_myr,
    previous_price_date,
    previous_price_myr,
    cast(price_myr - previous_price_myr as numeric(8, 2))  as change_myr,
    price_date - previous_price_date                        as days_since_previous
from with_previous
