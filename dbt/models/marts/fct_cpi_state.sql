-- One row per state, month and CPI division, with month-on-month and year-on-year change.
-- Changes are only computed when the comparison month really is 1 or 12 months earlier, so a
-- missing month yields null instead of a change measured across the gap.
with ordered as (
    select
        c.state_name,
        c.month_date,
        c.division_code,
        c.cpi_index,
        lag(c.month_date, 1)  over w as prev_month_date,
        lag(c.cpi_index, 1)   over w as prev_month_index,
        lag(c.month_date, 12) over w as prev_year_month_date,
        lag(c.cpi_index, 12)  over w as prev_year_index
    from {{ ref('stg_cpi_state') }} c
    window w as (partition by c.state_name, c.division_code order by c.month_date)
)
select
    {{ dbt_utils.generate_surrogate_key(['s.state_code', 'o.month_date', 'o.division_code']) }} as cpi_key,
    s.state_code,
    o.state_name,
    o.month_date,
    o.division_code,
    o.cpi_index,
    case when o.prev_month_date = o.month_date - interval '1 month'
         then cast(100.0 * (o.cpi_index / o.prev_month_index - 1) as numeric(8, 3))
    end as mom_pct,
    case when o.prev_year_month_date = o.month_date - interval '12 months'
         then cast(100.0 * (o.cpi_index / o.prev_year_index - 1) as numeric(8, 3))
    end as yoy_pct
from ordered o
left join {{ ref('dim_state') }} s
  on s.state_name = o.state_name
