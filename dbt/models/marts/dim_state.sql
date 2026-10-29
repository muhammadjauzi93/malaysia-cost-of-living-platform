-- One row per state or federal territory, from the reference seed.
-- The seed is the authority: a CPI state with a different spelling fails the relationship
-- tests on the facts instead of quietly becoming a seventeenth state.
select
    state_code,
    state_name,
    region
from {{ ref('state_reference') }}
