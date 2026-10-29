{#
  "Today" and "this month" on the Kuala Lumpur calendar, the same calendar the Python freshness
  checks use. The database server may run in UTC, where the date changes eight hours later.
  Postgres-specific (AT TIME ZONE); see DECISIONS.md #32.
#}
{% macro kl_today() -%}
    cast(current_timestamp at time zone 'Asia/Kuala_Lumpur' as date)
{%- endmacro %}

{% macro kl_current_month() -%}
    cast(date_trunc('month', current_timestamp at time zone 'Asia/Kuala_Lumpur') as date)
{%- endmacro %}
