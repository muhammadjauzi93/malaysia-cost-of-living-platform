-- Week-on-week change for every fuel type, computed from the previous announcement of THAT fuel.
-- LAG runs within each fuel type, so a new fuel type starts with no previous value rather than
-- inheriting another fuel's price.
-- Grain: one row per (price_date, fuel_type), the same as stg_fuel_prices.
--
-- event_description comes from the reviewed events seed (DECISIONS.md #47). The lateral
-- subquery aggregates, so a date matching both a fuel-specific event and an 'all' event still
-- yields one row: a plain join there would duplicate rows and break the grain.
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
    w.price_date,
    w.fuel_type,
    w.price_myr,
    w.previous_price_date,
    w.previous_price_myr,
    cast(w.price_myr - w.previous_price_myr as numeric(8, 2))  as change_myr,
    w.price_date - w.previous_price_date                        as days_since_previous,
    ev.event_description
from with_previous w
left join lateral (
    select string_agg(e.description, '; ' order by e.fuel_type) as event_description
    from {{ ref('fuel_price_events') }} e
    where e.event_date = w.price_date
      and e.fuel_type in (w.fuel_type, 'all')
) ev on true
