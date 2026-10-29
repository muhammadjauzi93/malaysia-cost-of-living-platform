-- One row per CPI division (COICOP 2018 codes 01 to 13, plus 'overall'), from the reference seed.
select
    division_code,
    division_name,
    division_code = 'overall' as is_all_items
from {{ ref('cpi_division_reference') }}
