-- Runs of consecutive announcements in which two fuel types carry exactly the same price.
-- Grain: one row per (fuel_a, fuel_b, run_start). fuel_a sorts before fuel_b.
--
-- A pair is compared only on dates when both fuels were announced. "Consecutive" means
-- consecutive shared announcements, so a monthly schedule still forms runs.
-- run_start_reviewed is true when the run's first date has a reviewed event for either fuel
-- (or for every fuel), which is how a legitimately identical series is accepted (DECISIONS.md #53).
with shared as (
    select
        a.price_date,
        a.fuel_type                 as fuel_a,
        b.fuel_type                 as fuel_b,
        a.price_myr = b.price_myr   as is_identical
    from {{ ref('stg_fuel_prices') }} a
    join {{ ref('stg_fuel_prices') }} b
      on b.price_date = a.price_date
     and b.fuel_type > a.fuel_type
),
numbered as (
    select
        price_date,
        fuel_a,
        fuel_b,
        is_identical,
        row_number() over (partition by fuel_a, fuel_b order by price_date)                    as seq,
        row_number() over (partition by fuel_a, fuel_b, is_identical order by price_date)      as seq_within
    from shared
),
islands as (
    -- Within one identical stretch, seq and seq_within rise together, so their difference is
    -- constant. A different price breaks the stretch and changes the difference.
    select
        fuel_a,
        fuel_b,
        price_date,
        seq - seq_within as island
    from numbered
    where is_identical
),
runs as (
    select
        fuel_a,
        fuel_b,
        min(price_date) as run_start,
        max(price_date) as run_end,
        count(*)        as announcements
    from islands
    group by fuel_a, fuel_b, island
)
select
    r.fuel_a,
    r.fuel_b,
    r.run_start,
    r.run_end,
    r.announcements,
    exists (
        select 1
        from {{ ref('fuel_price_events') }} e
        where e.event_date = r.run_start
          and e.fuel_type in (r.fuel_a, r.fuel_b, 'all')
    ) as run_start_reviewed
from runs r
