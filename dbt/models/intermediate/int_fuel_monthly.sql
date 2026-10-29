-- National fuel price per fuel type per calendar month, weighted by the DAYS each price was in
-- effect. Grain: one row per (month_date, fuel_type). National: see DECISIONS.md #33.
--
-- Why days, not announcements (DECISIONS.md #46): an announcement sets a price that holds until
-- the next one. Averaging announcements gave a month with no announcement (September 2018) no
-- price at all, and gave every announcement equal weight however long it applied. In June 2024
-- diesel was RM2.15 for 9 days and RM3.35 for 21: by announcements that averaged RM3.11, by days
-- RM2.99, which is what motorists actually paid on average.
--
-- How long a price applies:
--   * until the day before the same fuel's next announcement;
--   * a fuel's LAST price runs to today (Kuala Lumpur) if the fuel is still being published,
--     i.e. its last announcement is within `fuel_current_within_days` of the newest one;
--   * otherwise the fuel was discontinued, and its last price stops the day before the next
--     announcement of any fuel, so a retired subsidy scheme does not run on forever.
-- Every period is clipped at today, so no future day is ever priced.
with announcements as (
    select price_date, fuel_type, price_myr
    from {{ ref('stg_fuel_prices') }}
),
newest as (
    select max(price_date) as newest_date from announcements
),
periods as (
    select
        a.fuel_type,
        a.price_myr,
        a.price_date                                                          as valid_from,
        lead(a.price_date) over (partition by a.fuel_type order by a.price_date) as next_own_date
    from announcements a
),
bounded as (
    select
        p.fuel_type,
        p.price_myr,
        p.valid_from,
        least(
            case
                when p.next_own_date is not null
                    then p.next_own_date - 1
                when p.valid_from >= n.newest_date - {{ var('fuel_current_within_days', 14) }}
                    then {{ kl_today() }}
                else coalesce(
                    (select min(a2.price_date) from announcements a2 where a2.price_date > p.valid_from),
                    p.valid_from + 7
                ) - 1
            end,
            {{ kl_today() }}
        ) as valid_to
    from periods p
    cross join newest n
),
daily as (
    select
        b.fuel_type,
        b.price_myr,
        cast(d.day as date) as price_day
    from bounded b
    cross join lateral generate_series(b.valid_from, b.valid_to, interval '1 day') as d(day)
    where b.valid_to >= b.valid_from
),
monthly as (
    select
        cast(date_trunc('month', price_day) as date) as month_date,
        fuel_type,
        cast(avg(price_myr) as numeric(8, 4))       as avg_price_myr,
        min(price_myr)                              as min_price_myr,
        max(price_myr)                              as max_price_myr,
        count(*)                                    as days_priced
    from daily
    group by 1, 2
),
announcement_counts as (
    select
        cast(date_trunc('month', price_date) as date) as month_date,
        fuel_type,
        count(*)                                      as announcements_in_month
    from announcements
    group by 1, 2
)
select
    m.month_date,
    m.fuel_type,
    m.avg_price_myr,
    m.min_price_myr,
    m.max_price_myr,
    m.days_priced,
    coalesce(c.announcements_in_month, 0) as announcements_in_month
from monthly m
left join announcement_counts c
  on c.month_date = m.month_date
 and c.fuel_type  = m.fuel_type
