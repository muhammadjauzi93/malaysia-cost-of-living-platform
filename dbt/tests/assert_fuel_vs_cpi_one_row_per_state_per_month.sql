-- The mart is a full grid: every month has exactly one row per state, even before CPI is
-- published. A missing row would silently drop a state from the dashboard for that month.
select f.month_date, count(*) as rows_in_month
from {{ ref('fct_fuel_vs_transport_cpi') }} f
group by f.month_date
having count(*) <> (select count(*) from {{ ref('dim_state') }})
