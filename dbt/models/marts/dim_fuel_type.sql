-- One row per fuel type that has ever appeared in the source, built from the data so that a
-- new fuel column becomes a new row here without anyone editing a list.
-- The headline fuel for the project question is a variable (default ron95), not hard-coded.
with seen as (
    select
        fuel_type,
        min(price_date) as first_price_date,
        max(price_date) as last_price_date,
        count(*)        as weeks_published
    from {{ ref('stg_fuel_prices') }}
    group by 1
)
select
    fuel_type,
    case
        when fuel_type like 'ron%'    then 'petrol'
        when fuel_type like 'diesel%' then 'diesel'
        else 'other'
    end                                                     as fuel_family,
    fuel_type = '{{ var("headline_fuel_type", "ron95") }}'  as is_headline,
    first_price_date,
    last_price_date,
    weeks_published
from seen
