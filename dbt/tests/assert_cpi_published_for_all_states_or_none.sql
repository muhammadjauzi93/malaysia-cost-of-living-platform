-- OpenDOSM publishes a month's CPI for every state at once. So within a month, either all
-- sixteen states have transport CPI or none do. A mix means a state's rows failed to join,
-- most likely because the source renamed it, and the dashboard would quietly show that state
-- as 'awaiting_cpi' forever. Found by sabotage: renaming Pulau Pinang left this mart green.
select
    month_date,
    count(transport_cpi) as states_with_cpi,
    count(*)             as states
from {{ ref('fct_fuel_vs_transport_cpi') }}
group by month_date
having count(transport_cpi) not in (0, count(*))
