-- One row per state, month and COICOP division.
-- division_code stays text: '01' to '13' are codes, not numbers, and 'overall' is a value too.
select
    trim(state)                          as state_name,
    month_date,
    division                             as division_code,
    cast(cpi_index as numeric(10, 3))    as cpi_index,
    source                               as source_system,
    loaded_at
from {{ source('raw', 'cpi_state_monthly') }}
