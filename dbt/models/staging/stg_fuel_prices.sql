-- One row per fuel type per weekly price announcement.
-- Staging does three things only: rename, cast, trim. No joins, no business logic.
select
    price_date,
    lower(trim(fuel_type))          as fuel_type,
    cast(price_myr as numeric(6, 2)) as price_myr,
    source                          as source_system,
    loaded_at
from {{ source('raw', 'fuel_price_weekly') }}
