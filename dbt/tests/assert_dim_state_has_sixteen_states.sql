-- Malaysia has 13 states and 3 federal territories. Returns a row (fails) if the count differs.
select count(*) as states
from {{ ref('dim_state') }}
having count(*) <> 16
