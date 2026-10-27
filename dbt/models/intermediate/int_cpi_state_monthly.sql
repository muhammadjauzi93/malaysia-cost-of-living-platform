-- One row per state and month, with the three indices the project needs as columns:
-- the all-items index, food (division 01) and transport (division 07, COICOP).
-- Long-to-wide pivot happens here so the marts can join on one row per state and month.
-- Grain: one row per (state_name, month_date). Other divisions stay in staging.
select
    state_name,
    month_date,
    max(case when division_code = 'overall' then cpi_index end) as overall_cpi,
    max(case when division_code = '01'      then cpi_index end) as food_cpi,
    max(case when division_code = '07'      then cpi_index end) as transport_cpi
from {{ ref('stg_cpi_state') }}
where division_code in ('overall', '01', '07')
group by 1, 2
