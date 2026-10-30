-- SCD Type 2 invariant: a version ends exactly where the next one starts. A gap or an overlap
-- means "what was the price on day X" has zero or two answers.
with ordered as (
    select
        price_date,
        fuel_type,
        dbt_valid_from,
        dbt_valid_to,
        lead(dbt_valid_from) over (
            partition by price_date, fuel_type order by dbt_valid_from
        ) as next_valid_from
    from {{ ref('snap_fuel_prices') }}
)
select *
from ordered
where next_valid_from is not null
  and dbt_valid_to is distinct from next_valid_from
