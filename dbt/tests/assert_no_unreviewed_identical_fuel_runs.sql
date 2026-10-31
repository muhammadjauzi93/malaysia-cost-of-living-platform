-- Quality rule (DECISIONS.md #53). Returns every run of identical prices across two fuel types
-- that lasts at least `identical_run_min_announcements` announcements and whose first date has
-- no reviewed event. Each returned row is one run to investigate. Zero rows means the test passes.
--
-- Why this exists: diesel_eastmsia matched peninsular diesel to the sen for five weeks, and the
-- range and week-on-week checks missed it. Two unrelated fuels do not repeat a price to the sen.
select *
from {{ ref('int_fuel_identical_runs') }}
where announcements >= {{ var('identical_run_min_announcements', 3) }}
  and not run_start_reviewed
