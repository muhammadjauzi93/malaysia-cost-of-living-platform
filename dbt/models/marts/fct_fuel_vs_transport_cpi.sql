-- The project question in one table: one row per state per month, putting the NATIONAL headline
-- fuel price (var headline_fuel_type, default ron95) next to each state's transport CPI.
--
-- Built on a complete grid of months x states, so a month with no fuel or no CPI yet is a row
-- with nulls, never a missing row. That keeps every LAG below exactly one calendar month.
--
-- fuel_month_status = 'partial' for the current Kuala Lumpur month, whose fuel average is built
-- from the weeks published so far. cpi_status = 'awaiting_cpi' until OpenDOSM publishes the
-- month (around the 19th of the next month). Both are as of `built_at`.
with fuel as (
    select month_date, avg_price_myr, weekly_observations
    from {{ ref('int_fuel_monthly') }}
    where fuel_type = '{{ var("headline_fuel_type", "ron95") }}'
),
cpi as (
    select state_name, month_date, transport_cpi, overall_cpi
    from {{ ref('int_cpi_state_monthly') }}
),
bounds as (
    select
        greatest((select min(month_date) from fuel), (select min(month_date) from cpi)) as first_month,
        greatest((select max(month_date) from fuel), (select max(month_date) from cpi)) as last_month
),
months as (
    select cast(generate_series(first_month, last_month, interval '1 month') as date) as month_date
    from bounds
),
national_fuel as (
    -- Computed on the month spine, so a month without announcements breaks the chain with nulls.
    select
        m.month_date,
        f.avg_price_myr                                               as fuel_avg_price_myr,
        f.weekly_observations                                         as fuel_weekly_observations,
        f.avg_price_myr - lag(f.avg_price_myr) over (order by m.month_date) as fuel_mom_change_myr
    from months m
    left join fuel f on f.month_date = m.month_date
),
grid as (
    select m.month_date, s.state_code, s.state_name, s.region
    from months m
    cross join {{ ref('dim_state') }} s
),
joined as (
    select
        g.month_date,
        g.state_code,
        g.state_name,
        g.region,
        n.fuel_avg_price_myr,
        n.fuel_weekly_observations,
        n.fuel_mom_change_myr,
        lag(n.fuel_mom_change_myr) over st                          as fuel_mom_change_prev_month_myr,
        c.transport_cpi,
        c.overall_cpi,
        100.0 * (c.transport_cpi / lag(c.transport_cpi) over st - 1) as transport_cpi_mom_pct,
        100.0 * (c.overall_cpi   / lag(c.overall_cpi)   over st - 1) as overall_cpi_mom_pct
    from grid g
    join national_fuel n on n.month_date = g.month_date
    left join cpi c
      on c.state_name = g.state_name
     and c.month_date = g.month_date
    window st as (partition by g.state_code order by g.month_date)
)
select
    {{ dbt_utils.generate_surrogate_key(['state_code', 'month_date']) }} as state_month_key,
    month_date,
    state_code,
    state_name,
    region,
    '{{ var("headline_fuel_type", "ron95") }}'                 as fuel_type,
    cast(fuel_avg_price_myr as numeric(8, 4))                   as fuel_avg_price_myr,
    fuel_weekly_observations,
    cast(fuel_mom_change_myr as numeric(8, 4))                  as fuel_mom_change_myr,
    cast(fuel_mom_change_prev_month_myr as numeric(8, 4))       as fuel_mom_change_prev_month_myr,
    transport_cpi,
    cast(transport_cpi_mom_pct as numeric(8, 3))                as transport_cpi_mom_pct,
    overall_cpi,
    cast(overall_cpi_mom_pct as numeric(8, 3))                  as overall_cpi_mom_pct,
    case when month_date = {{ kl_current_month() }} then 'partial' else 'complete' end
                                                                as fuel_month_status,
    case when transport_cpi is null then 'awaiting_cpi' else 'published' end
                                                                as cpi_status,
    current_timestamp                                           as built_at
from joined
