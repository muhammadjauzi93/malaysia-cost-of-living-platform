{#
  dbt-postgres stamps snapshots with `current_timestamp::timestamp without time zone`, which
  throws the time zone away, while raw.loaded_at (the snapshot's updated_at) is timestamptz.
  dbt warns about the mismatch on every snapshot run, and mixing the two types means the
  session's TimeZone setting silently decides how they compare.

  This override keeps the snapshot clock as timestamptz, like every other timestamp in the
  project (Tier 1: store an instant with its time zone). It is only used by snapshots.
  See DECISIONS.md #48.
#}
{% macro postgres__snapshot_get_time() -%}
  {{ current_timestamp() }}
{%- endmacro %}
