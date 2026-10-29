-- Singular test: returns the rows that FAIL. Zero rows means the test passes.
-- Every state-month that staging holds for the three divisions we keep must come through the
-- pivot. A dropped month here would silently shorten the fuel-vs-transport series.
with staged as (
    select distinct state_name, month_date
    from {{ ref('stg_cpi_state') }}
    where division_code in ('overall', '01', '07')
),
built as (
    select state_name, month_date
    from {{ ref('int_cpi_state_monthly') }}
)
select s.state_name, s.month_date
from staged s
left join built b
  on b.state_name = s.state_name
 and b.month_date = s.month_date
where b.state_name is null
