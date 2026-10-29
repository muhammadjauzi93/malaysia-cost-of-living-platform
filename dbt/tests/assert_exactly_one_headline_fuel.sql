-- The fuel-vs-CPI mart needs exactly one headline fuel, and it must exist in the data.
-- Fails if var headline_fuel_type names a fuel type the source never published.
select count(*) as headline_fuels
from {{ ref('dim_fuel_type') }}
where is_headline
having count(*) <> 1
