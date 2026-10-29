-- DECISIONS.md #33: fuel prices are national. In every month, every state must carry the same
-- fuel average; two different values would mean a join fanned out or a state-level price leaked in.
select month_date, count(distinct fuel_avg_price_myr) as distinct_prices
from {{ ref('fct_fuel_vs_transport_cpi') }}
group by month_date
having count(distinct fuel_avg_price_myr) > 1
