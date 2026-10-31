-- Can the data on the dashboard be trusted? One row per source, always all three, even before a
-- source has any rows. The dashboard reads it to label synthetic data and to show how recent each
-- source is and when the pipeline last loaded it (DECISIONS.md #57, #62).
--
-- It deliberately does not read int_fuel_identical_runs. On the real data the identical-price
-- test fails by design (DECISIONS.md #53a), and dbt build skips every model downstream of a
-- failing test, so this mart would disappear exactly when the data most needs a warning.
with source_list as (
    select source_name, source_label
    from (values
        ('fuel_prices', 'Fuel prices (data.gov.my)'),
        ('cpi_state',   'State CPI (OpenDOSM)'),
        ('fx_rates',    'Exchange rates (BNM)')
    ) as v (source_name, source_label)
),
source_rows as (
    select 'fuel_prices' as source_name, source_system, price_date as data_date
    from {{ ref('stg_fuel_prices') }}
    union all
    select 'cpi_state', source_system, month_date
    from {{ ref('stg_cpi_state') }}
    union all
    select 'fx_rates', source_system, rate_date
    from {{ ref('stg_fx_rates') }}
),
volumes as (
    select
        source_name,
        count(*)                                                as row_count,
        -- The label load-sample writes (col_pipeline.sample_data.SAMPLE_SOURCE_LABEL).
        count(*) filter (where source_system = 'synthetic-sample') as synthetic_row_count,
        min(data_date)                                          as earliest_data_date,
        max(data_date)                                          as latest_data_date
    from source_rows
    group by source_name
),
runs as (
    select
        pipeline_name                                                   as source_name,
        max(started_at)                                                 as last_run_at,
        (array_agg(status order by started_at desc, run_id desc))[1]    as last_run_status,
        max(ended_at) filter (where status = 'success')                 as last_success_at,
        count(*) filter (where status = 'failed')                       as failed_runs
    from {{ source('ops', 'pipeline_runs') }}
    group by pipeline_name
)
select
    s.source_name,
    s.source_label,
    coalesce(v.row_count, 0)                    as row_count,
    coalesce(v.synthetic_row_count, 0)          as synthetic_row_count,
    coalesce(v.synthetic_row_count, 0) > 0      as has_synthetic_rows,
    v.earliest_data_date,
    v.latest_data_date,
    r.last_run_at,
    r.last_run_status,
    r.last_success_at,
    coalesce(r.failed_runs, 0)                  as failed_runs,
    now()                                       as built_at
from source_list s
left join volumes v on v.source_name = s.source_name
left join runs r    on r.source_name = s.source_name
