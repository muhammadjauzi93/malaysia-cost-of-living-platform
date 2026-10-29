-- A partial month is the current month, so it can only ever be the newest month in the mart.
-- A partial flag on an older month means the calendar logic is wrong.
select month_date
from {{ ref('fct_fuel_vs_transport_cpi') }}
where fuel_month_status = 'partial'
  and month_date < (select max(month_date) from {{ ref('fct_fuel_vs_transport_cpi') }})
