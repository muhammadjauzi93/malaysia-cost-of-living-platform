-- SCD Type 2 invariant: each (price_date, fuel_type) has exactly one CURRENT version
-- (dbt_valid_to is null). Two current versions would double-count; zero would lose the price.
select price_date, fuel_type, count(*) as current_versions
from {{ ref('snap_fuel_prices') }}
where dbt_valid_to is null
group by price_date, fuel_type
having count(*) <> 1
