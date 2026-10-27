{#
  dbt's default builds schema names as <target schema>_<custom schema>, e.g. analytics_staging.
  This override uses the custom schema as given, so the warehouse has plain schemas named
  after their layer: staging, intermediate, marts. See DECISIONS.md #31.

  Trade-off: two people sharing one database would overwrite each other's models, because
  the target schema no longer separates them. Here there is one developer and CI uses its
  own throwaway database, so the readable names win. With a team, give each developer a
  database or restore the default prefixing for the dev target.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
