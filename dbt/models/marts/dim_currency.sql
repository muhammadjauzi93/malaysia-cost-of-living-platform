-- One row per tracked currency, from the reference seed.
select
    currency_code,
    currency_name
from {{ ref('currency_reference') }}
