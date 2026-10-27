-- National fuel price per fuel type per calendar month: the average of that month's weekly
-- announcements, plus the range inside the month.
-- Grain: one row per (month_date, fuel_type). National, not per state: see DECISIONS.md #33.
select
    cast(date_trunc('month', price_date) as date)  as month_date,
    fuel_type,
    cast(avg(price_myr) as numeric(8, 4))           as avg_price_myr,
    min(price_myr)                                  as min_price_myr,
    max(price_myr)                                  as max_price_myr,
    count(*)                                        as weekly_observations
from {{ ref('stg_fuel_prices') }}
group by 1, 2
