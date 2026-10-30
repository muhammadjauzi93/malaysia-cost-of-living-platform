# Design decisions

Short records of choices made, the alternatives, and what was traded away.
Newest at the bottom. Phase 1 = fuel price ingestion, Phase 2 = orchestration and quality,
Phase 3 = the dbt warehouse (#30 onwards).

---

### 1. Store fuel prices in long format, not wide

**Context.** The API returns one wide row per week (`ron95`, `ron97`, `diesel`, ...).
New columns have appeared over time: `diesel_eastmsia` after the June 2024 diesel
subsidy change, and `ron95_budi95` / `ron95_skps` after the 2025 RON95 subsidy change.

**Decision.** Melt to one row per `(price_date, fuel_type)`.

**Why.** A new fuel column becomes new *rows*, not a new *column*. No `ALTER TABLE`, no code
change, no broken pipeline. `test_new_fuel_columns_flow_through_without_code_changes`
proves it.

**Trade-off.** Analysts usually want wide. The pivot moves to a dbt model in Phase 3.

---

### 2. Load only `series_type = level`

The dataset also publishes `change_weekly` rows. Those are derivable with `LAG()` and
mixing them into the same column would make every query need a filter. Raw stores facts,
not derivations. The filter is applied in the API call **and** in transform, because
an API filter is a convenience, not a guarantee.

---

### 3. Watermark with a 21-day lookback

Incremental runs request `date_start = max(price_date) - 21 days`. Re-fetching three
weeks costs ~3 rows and catches any price the source revises after publishing. The upsert
makes the overlap harmless. Configurable with `LOOKBACK_DAYS`. *(Superseded by #19: each
source now owns its lookback and `LOOKBACK_DAYS` is gone.)*

---

### 4. Stage-then-merge with `IS DISTINCT FROM`

Rows go into a temp table, then a single `INSERT ... ON CONFLICT DO UPDATE ... WHERE
t.price_myr IS DISTINCT FROM EXCLUDED.price_myr`. Two benefits over a row-by-row upsert:
`loaded_at` only changes when a value really changed (so it means something), and
`RETURNING (xmax = 0)` gives exact inserted/updated counts for the run log.

**Trade-off.** `xmax = 0` is a Postgres-specific idiom. A move to another warehouse
would use its native `MERGE` instead; only `load.py` would change.

---

### 5. Land the raw payload before transforming

Every run writes the untouched JSON to `data/landing/fuelprice/`. If transform has a bug,
fix it and replay from disk instead of calling an API limited to 4 requests/minute.
This folder becomes the bronze layer in later phases.

---

### 6. Postgres on host port 5433

5432 is often taken by a local Postgres or another learning container. The container
still listens on 5432 internally; only the host mapping changes. Configurable via
`POSTGRES_PORT` in `.env` (see #12).

---

### 7. An empty incremental extract is a failure

With a 21-day lookback on weekly data, zero rows means the source has stalled or the
query is wrong. The run fails and is logged as `failed`, rather than "succeeding" with
nothing. Loud failure is a feature.

---

## Portability revision

The first version assumed a Unix machine with `python3.11` and `make` installed. It failed
for anyone on Python 3.12/3.13, on Windows (`.venv/bin/python` is `.venv\Scripts\python.exe`
there), or without `make`. CI only tested Linux on 3.11, so none of this was caught.
Entries 8 to 13 fix that. Pipeline logic did not change.

### 8. A `col-pipeline` console script instead of venv paths

`[project.scripts]` makes pip/uv generate a `col-pipeline` executable inside the venv on every
OS (`col-pipeline.exe` on Windows). Nobody types an OS-specific path. Subcommands: `run`,
`runs`, `check`. `python -m col_pipeline.main` still works. *(Phase 2 removed `main.py`;
`python -m col_pipeline.cli` is the module form now.)*

`runs` reads `ops.pipeline_runs` through SQLAlchemy instead of shelling out to `psql`, which
removed a container dependency and a layer of shell quoting. `check` exists because "it
doesn't run on my machine" deserves a diagnostic, not a stack trace.

### 9. uv as the recommended setup, pip as a supported fallback

`uv sync` creates the venv, installs a Python if needed, and installs from `uv.lock`;
`uv run <cmd>` runs inside the venv without activation. The commands are identical in
PowerShell and bash. `uv.lock` also closes an earlier gap: installs are now reproducible.

**Trade-off.** One more tool to install. So `python tasks.py setup --pip` (or no uv on PATH)
uses only `venv` + `pip`, and CI tests that path on Windows and Linux too.

No `.python-version` pin: any Python >= 3.11 works and CI proves 3.11 to 3.13. The lockfile
pins packages; the CI matrix covers interpreters.

### 10. `tasks.py` replaces the Makefile

A stdlib-only Python script runs anywhere Python runs, which is already a prerequisite.
Commands are passed to `subprocess` as lists, so there is no shell quoting to differ between
PowerShell and bash. It finds `.venv`'s interpreter itself, so tasks work without activating.

**Alternatives rejected.** Keeping `make` plus a PowerShell script means two files that drift.
`just` and `nox` are good tools but are another install for a handful of commands.

### 11. Dev dependencies as a PEP 735 group

`[dependency-groups] dev` is installed by `uv sync` by default. The pip fallback in
`tasks.py` reads the same list from `pyproject.toml` with `tomllib`, so it is declared once.

### 12. The database password is written once

`DB_URL` is now optional. If absent, it is built from the `POSTGRES_*` variables that docker
compose already reads, using SQLAlchemy's `URL.create`, which escapes characters like `@`
and `:` that would corrupt a hand-built URL. An explicit `DB_URL` still wins, for CI and cloud.

### 13. CI matrix that matches the promise

Linux on 3.11, 3.12 and 3.13 with integration tests; Windows with uv (unit tests, console
script, `tasks.py`); and the pip fallback on both OSes. Windows cannot run the integration
tests because GitHub service containers only work on Linux runners. Windows' risk is
installation and invocation, which these jobs cover.

---

## Phase 2: more sources, orchestration, data quality

### 14. A source is data, not a script

A `Source` bundles a `TableSpec` (table, key, value and date columns), `QualityRules`, and two
functions: `fetch` and `to_frame`. One generic loader, runner and Dagster factory serve every
source. Adding a source is one module; nothing else changes.

**Trade-off.** The loader builds SQL from identifiers in the spec. That is safe only because
`TableSpec` validates every identifier at import time, and values still travel as parameters.

### 15. Structure raises; plausibility reports

`to_frame` enforces *structure* (required columns, types, unique keys) and raises
`DataContractError`. `QualityRules` judge *plausibility* (ranges, volume, ordering,
completeness) and return results with a severity: ERROR blocks the load, WARN is only reported.

Phase 1 checked the fuel price range inside the transform. It moved to the rules, so every
source works the same way. That is the one Phase 1 test that changed; the load is still blocked.

### 16. FX: the daily run asks for "latest", not for a date

BNM's latest-rates endpoint returns the most recent published day. On a weekend it simply
returns Friday again, and the upsert makes that a no-op. No calendar logic, no weekend special
case. This refines #7: FX is never empty, so staleness is caught by freshness (#22) instead.

### 17. FX history is opt-in, and a 404 means "no data"

`--since` calls the per-date endpoint for each weekday and tracked currency. BNM answers 404
for days it published nothing (holidays), so that 404 is treated as "no rows", not an error.
Everywhere else a 404 still fails loudly. The per-date path comes from secondary sources and is
kept off the daily path for that reason; `probe-bnm` shows what the live API returns.

### 18. FX rates stored as BNM quotes them

`raw.fx_rate_daily` keeps `unit` (JPY is quoted per 100) and the BNM session in the key.
"Ringgit per 1 unit" is a derivation, so it belongs in dbt. Session 1700, the last interbank
snapshot, makes day-over-day comparisons consistent. Five currencies, chosen for the question:
USD (oil is priced in it), SGD and CNY (trade partners), EUR and JPY (exercise the unit logic).

### 19. Each source owns its lookback

Fuel re-reads 21 days (weekly data, revisions), CPI 92 days (monthly, occasional revisions), FX
none (it reads "latest"). One global `LOOKBACK_DAYS` could not fit all three, so it was removed.

### 20. Two retry layers on different time scales

tenacity in the HTTP client retries 429, 5xx and network errors after 15, 30 and 60 seconds.
Dagster retries a failed landing step after 5 and then 10 minutes, long enough for an outage or
a rate-limit window to clear. Worst case: 3 Dagster attempts x 4 HTTP attempts = 12 requests over
about 20 minutes. The load step retries after 30 seconds, for database blips.

Deterministic failures are never retried: a failed gate, bad run configuration, a data contract
error. Tests prove a failed gate makes exactly one attempt.

### 21. The gate lives inside the landing asset

The quality gate is a *blocking* check emitted by the landing asset itself. When it fails,
Dagster fails the step and the load asset never runs; the landed file stays for inspection.

**Alternative rejected.** A separate `@asset_check` cannot receive the asset it checks as an
input in Dagster 1.13, so it would have to re-discover the landed file. Inline keeps one handle.
The load step validates again anyway, so it is safe even if someone runs it on its own.

### 22. Alert on staleness, not only on failure

A pipeline can succeed every day while its source has stopped publishing. Each raw table has a
freshness check: blocking, so a breach fails the run and triggers the alert. It runs after every
load and again in a daily monitor job, which catches a schedule that silently stopped.

### 23. Schedules follow each source's rhythm

* **Fuel: Thursday and Friday 10:00.** The dataset's weekly dates fall on Thursdays in every
  sample checked; Friday catches a late publication.
* **CPI: 14:30 on the 19th to 25th.** OpenDOSM lists the 19th, 12:30 as the next release; the
  window covers releases that move around weekends.
* **FX: weekdays 19:00.** After BNM's 17:00 snapshot. The exact publish time is an assumption.

Extra runs cost a few API calls and change nothing, because every load is idempotent.

### 24. Batched staging inserts

`execute_values` sends 2,000 rows per statement instead of one round trip per row. Measured on
a 43,008-row CPI history: 4.0s became 0.9s, and the gap grows with network latency (Docker
Desktop, a cloud database). **Trade-off:** the loader is now tied to psycopg2; a move to
psycopg 3 would use `COPY` instead, and only `load.py` changes.

### 25. Abandoned runs are closed automatically

A killed process leaves its run at `running` forever. Each new run of a source first marks that
source's runs older than six hours as failed, with an explanatory message. Runs of other sources
and recent runs are never touched, so a genuinely concurrent run is safe.

### 26. Dagster runs on the host, not in Docker (for now)

`python tasks.py dagster` starts `dagster dev` with `DAGSTER_HOME` inside the repository, so
schedule state and run history survive a restart. Only Postgres runs in Docker. Containerising
Dagster belongs with the cloud work in a later tier, where it will run on managed infrastructure.

### 27. Alerting must never break anything

Slack is optional (`SLACK_WEBHOOK_URL`). Sending never raises: a Slack outage cannot turn one
failed run into two. Failures are logged by exception type only, because the exception text can
contain the webhook URL, which is a secret.

### 28. `init-db` evolves an existing database

Docker applies `schema.sql` only to a new volume. `init-db` applies it to any database, and every
statement is `IF NOT EXISTS`, so it is safe to repeat. It cannot rename or alter columns; when the
schema needs that, the project will need a real migration tool.

### 29. A retried load can turn a failed run into a success

Dagster retries the load step with the same landed batch, so with the same `run_id`. The first
attempt marks the run `failed`; if the retry then loads the data, the run log must say `success`,
or it contradicts both Dagster and the table. So `succeed` accepts a `running` or `failed` run
and clears the error message, while `fail` still only closes a `running` one. A `success` is
never overwritten. **Alternative rejected:** a new run row per load attempt. It would make the
landing and load phases disagree about which run a batch belongs to.

---

## Phase 3: the dbt warehouse

### 30. Source freshness is judged on the newest data date, not on `loaded_at`

**Context.** The Phase 3 plan was `dbt source freshness` on `loaded_at`, the obvious column.
But the loader only moves `loaded_at` when a value really changes (#4), and fuel prices can sit
unchanged for weeks. In the sample data RON95 has 4 distinct prices in 104 weekly rows. Judged
on `loaded_at`, a healthy pipeline would look stale for most of the year.

**Decision.** `loaded_at_field` is the newest data date (`price_date`, `month_date`,
`rate_date`), with the same limits Phase 2 enforces in Python: fuel 10 days, CPI 90, FX 5. Warn
thresholds sit just below (8, 85 and 4 days).

**Trade-off.** A data date cannot tell "the source stopped publishing" from "our pipeline
stopped"; `ops.pipeline_runs` answers that. A date is read as midnight UTC, up to eight hours
off the Kuala Lumpur calendar the Python check uses, which does not matter at limits of days.
**Alternative rejected:** a `last_seen_at` column touched on every run. It would rewrite every
row on every run and destroy the honest `loaded_at` of #4.

### 31. Plain schema names per layer

dbt normally names schemas `<target>_<custom>`, giving `analytics_staging`. A one-file macro
override gives `staging`, `intermediate` and `marts`. **Trade-off:** developers sharing a database
would overwrite each other's models. There is one developer and CI uses a throwaway database;
with a team, restore the prefix for the dev target.

### 32. `dbt-postgres`, in the same database as `raw`

One database to run, back up and explain, and the raw tables are already there. dbt only reads
`raw`; it owns every schema downstream of it. **Trade-off:** analytical queries run on a
row-store and compete with ingestion for one instance. At tens of thousands of rows that is
irrelevant; DuckDB would win at scale and stays an option because the models use portable SQL
and `dbt_utils` macros.

### 33. National fuel prices join state CPI by month; fuel is not copied to each state

A pump price has no state. Copying one national RON95 price onto sixteen states would make a
state-level fact table look like it held sixteen independent observations. So the grains are:
`fct_fuel_prices` is one row per `(price_date, fuel_type)`, `fct_cpi_state` is one row per
`(state, month, division)`, and `fct_fuel_vs_transport_cpi` is one row per `(state, month)`,
carrying the *national* monthly fuel average as a column. **Trade-off:** the dashboard needs one
join, and states differ in the answer only through their CPI. **Open point:** the source also
has a series named `diesel_eastmsia`, so "national" is safe for RON95 and RON97 but must be
checked for diesel before it is used. The headline fuel (RON95) is an assumption to confirm
against live data.

### 34. Synthetic sample data for CI and dbt development, with three safeguards

CI cannot reach the real APIs, and the test fixtures hold two states, which cannot exercise a
rule like "every month has 16 states". `col_pipeline.sample_data` generates a deterministic
dataset with the real shapes (16 states, 14 divisions, several fuel types, five currencies, JPY
per 100) and loads it through the real quality gate and loader. Safeguards: it is deterministic;
every row is labelled `source = 'synthetic-sample'`, which flows through dbt; and it refuses to
load into a database that already has data. **Trade-off:** synthetic data cannot reveal real-world
surprises (a renamed state, a new division), so it proves the *tests and models work*, not that
the *real data is clean*. The first live run still has to happen. **Alternative rejected:** a
committed CSV of real data. It would age past the freshness limits, so freshness could never be
tested, and it adds a licensing question.

### 35. Tests fail on broken contracts and only warn on "something new"

Keys, nulls, plausible ranges and fixed code lists (CPI divisions, BNM sessions) are errors. A new
fuel type, an untracked currency or an unusual quoting unit are warnings. The pipeline is
designed so that a new fuel column becomes new rows (#1), so a new value must be visible but must
not block the build. **Trade-off:** a warning can be ignored forever. Review `WARN` lines
whenever `dbt build` prints them, and promote a warning to an error once its list is stable.
Verified by sabotage: seven deliberate faults produced five errors and three warnings.

### 36. dbt shares the project environment and lock file

One `uv.lock` serves Dagster and dbt. Adding dbt changed no existing pin and added 36 packages;
`dbt-core` is capped below 2 so a future `uv lock --upgrade` cannot jump a major version.
`dbt_utils` is installed from git at an exact tag, and `package-lock.yml` is committed, so what
runs is a specific commit anyone can inspect. **Trade-off:** a future Dagster and dbt version
conflict would force dbt into its own environment, which is how many teams run it anyway.
Installing from git needs `git` on the PATH, which this project already requires.

### 37. Intermediate models are views; marts will be tables

Intermediate models sit between staging and the marts and only exist to feed them, so nothing
reads them directly. A view costs no storage and is always current. **Trade-off:** each query
recomputes the joins and windows. The CPI pivot reads 8,064 raw rows and returns 576 (16 states by 36 months), which is milliseconds. When the mart
tables are built, the cost is paid once per run rather than per query, which is why the marts
are tables. Revisit if an intermediate model ever scans millions of rows.

### 38. CPI is pivoted to one row per state and month, keeping three divisions

The marts join on state and month, so a long table (one row per division) would need a pivot in
every join. `int_cpi_state_monthly` pivots once, into `overall_cpi`, `food_cpi` and
`transport_cpi`. Division 07 is COICOP Transport, the index that should move with pump prices;
01 is Food, the usual second cost-of-living measure. The other eleven divisions stay in staging
for later. **Trade-off:** a new division of interest means changing this model, not configuration.
A singular test asserts that every state and month in staging survives the pivot, so a dropped
month shows up as a failure, not as a shorter chart.

### 39. FX is converted to ringgit per one unit, from one BNM session, with a test for JPY

BNM quotes JPY per 100, so `middle_rate / quote_unit` puts every currency on the same basis. The
model filters to one session (`fx_session`, default 1700), because the raw key includes the
session: a future change to `BNM_SESSION` could otherwise average two snapshots of the same day.

The JPY case is the instructive one. Sabotage test: a source that quotes JPY per 1 instead of per
100 produces a value of about RM3.00 instead of RM0.03. That passes the generic range test
(0.01 to 100), so a dedicated singular test asserts that JPY is below RM0.10 per unit. **Lesson:** a
range test only catches values outside a band; a unit error can land inside it. Each unit trap
needs its own assertion.

### 40. Fuel changes are measured within each fuel type, and partial months are visible

`LAG` is partitioned by `fuel_type`, so a fuel that appears late (a new subsidy scheme) starts with
no previous value. Taking the previous row of the table instead would compare RON95 with diesel the
first time a new fuel appears. (Monthly averages were later reworked, see #46.) Monthly averages carry `weekly_observations`: a full month has four
or five announcements, so the current month is visibly partial until it closes. **Trade-off:** the
dashboard must show or exclude partial months explicitly; it cannot assume every month is complete.
A warning test flags a gap of more than two weeks, which usually means a missed week in the source.

### 41. The headline fuel is RON95, held in a variable

The project question uses one fuel, and RON95 is the grade most households buy. It is the
`headline_fuel_type` variable (default `ron95`), not a literal in the SQL, so switching the whole
mart to another series is one setting. A test asserts that exactly one fuel is flagged and that
it exists in the data, so a typo fails the build instead of producing an empty fuel column.
**Open point:** the source also has `ron95_budi95`. Which RON95 series best matches what
households paid after the subsidy change needs checking; the variable makes either answer cheap.

### 42. Partial months are labelled by the calendar, not by counting weeks

The current Kuala Lumpur month is `fuel_month_status = 'partial'`; every earlier month is
`complete`. Counting weekly announcements would have been the alternative, but real data already
shows gaps of more than two weeks (20 rows in the first real run: the 2018 schedule change, see #46
and #47), and a count would label any such old month partial. Months without published CPI are a separate status, `awaiting_cpi`, because a month
can be complete for fuel while its CPI is still weeks away. **Trade-off:** both labels are
correct as of `built_at`, the last build. If dbt does not run after a month ends, last month stays
labelled partial until the next build.

### 43. Reference lists are seeds; fuel types come from the data

States, CPI division names and currencies change only when the real world does, so they are
small CSV seeds in version control. `dim_state` takes the seed as the authority, so a CPI state
spelled differently fails a relationship test instead of quietly becoming a seventeenth state.
Fuel types are the opposite: new ones arrive with the source (#1), so `dim_fuel_type` is built
from the data and grows on its own. Division codes are pinned to text in the seed config, because
dbt would otherwise infer `01` as the integer 1. **Trade-off:** division names follow COICOP 2018
and should be checked against OpenDOSM's own labels before they appear on a dashboard.

### 44. The weekly fuel fact is incremental, with a 21-day lookback

`fct_fuel_prices` reprocesses only the last 21 days of announcements and replaces those keys
(dbt-postgres' delete+insert), matching the ingestion lookback (#3). Proof, on the sample: a re-run
rewrote exactly the 16 rows inside the window and left the other 326 physically untouched (checked
with Postgres row versions), with no keys gained or lost. CI re-runs the model on every pull request
so its uniqueness tests run on the incremental path. **Trade-off:** a revision older than 21 days
does not reach the mart until `--full-refresh`. Shown by test: a price corrected 60 days back stayed
stale through an incremental run and was fixed by a full refresh. The snapshot (next step) keeps the
history of such revisions. The other facts are small and rebuilt in full, which keeps their month-on-
month and year-on-year windows simple.

### 45. The fuel-vs-CPI mart is a complete month-by-state grid

`fct_fuel_vs_transport_cpi` starts from every month crossed with every state, then left-joins fuel
and CPI. A month with no fuel or no CPI yet is a row with nulls, never a missing row, so every `LAG`
compares with exactly the previous calendar month. The national fuel price sits on every state's
row, with last month's change beside this month's transport CPI change, which is the comparison
the project question asks for. **Found by sabotage:** renaming Pulau Pinang in the source left this
mart green (the state's rows simply showed `awaiting_cpi` forever); only another model's test failed.
A test now asserts that in every month either all states have CPI or none do, so the mart catches
it on its own.

### 46. Monthly fuel prices are weighted by the days each price was in effect

**Found on real data.** The first live run left September 2018 with no RON95 price: it was
announced on 30 August and next on 1 October, and `int_fuel_monthly` averaged the announcements
made *within* a month. Yet motorists paid RM2.20 every day of September. The same flaw distorted
every irregular month. June 2024 diesel was RM2.15 for 9 days and RM3.35 for 21; averaging the
five announcements gave RM3.11, weighting by days gives RM2.99.

**Decision.** An announcement sets a price that holds until the same fuel's next announcement.
The model expands each price to the days it applied and averages by day. A fuel's latest price
runs to today (Kuala Lumpur) if the fuel is still published; if its last announcement is more
than 14 days older than the newest one, it was discontinued and stops at the next announcement
of any fuel, so a retired scheme does not run on forever. No future day is ever priced. The
monthly columns are now `days_priced` and `announcements_in_month`, replacing
`weekly_observations`; the mart's `fuel_avg_price_myr` is never null and its test is an error.

**How it is tested.** dbt unit tests feed fixed rows in and compare exact rows out, with "today"
pinned: September 2018 (RM2.20 on 30 days, zero announcements), June 2024 diesel (exactly RM2.99),
a discontinued fuel stopping, and the current month stopping at today. The synthetic sample is
perfectly regular and could never have exposed this. Verified that the tests can fail: expecting
RM3.11 fails with "actual differs from expected".

**Trade-off.** A price carried forward is an assumption: if the source silently skips a week, the
model keeps the old price rather than showing a gap. Source freshness (10 days) and the gap
warning cover that. The view expands every price to daily rows (tens of thousands for the full history), which
is trivial here; at a much larger scale it would be materialised as a table.

### 47. Reviewed fuel events are a seed; warnings fire only for something new

**Found on real data.** Two warnings fired on every build: diesel's RM1.20 jump on 10 June 2024,
and 20 long gaps in 2018-19 when announcements moved from weekly to monthly. Both are real events,
not errors. A warning that fires every run teaches people to ignore warnings, including the one
that matters.

**Decision.** `seeds/fuel_price_events.csv` lists reviewed events: date, fuel type (or `all`),
kind, and a one-line description of what the data shows. `int_fuel_weekly_changes` attaches the
description, the two warning tests skip rows that have one, and `fct_fuel_prices` carries it so
the dashboard can annotate the chart. The join aggregates, so a date matching two events still
gives one row (a unit test proves it). Tested on the sample: a reviewed diesel jump stayed silent
while an invented RON97 jump on the same day still warned.

**Rule.** Add a row only after looking at the data and understanding it, never to silence a
warning. Descriptions state what the data shows, not background claims. **Trade-off:** someone
must review new warnings. That is the point: the review is now recorded in version control.

**Upgrade note.** `fct_fuel_prices` gained a column. An incremental model adds the column but only
fills it for rows inside the lookback window, so existing databases need one
`python tasks.py dbt-build --full-refresh` to fill it for older rows.

### 48. The fuel price snapshot uses the timestamp strategy on loaded_at

`snap_fuel_prices` keeps every version of every price (SCD Type 2), because the raw upsert
overwrites a revised price. It reads the raw table directly, so history is kept before any
cleaning. **Strategy:** timestamp on `loaded_at`, not a column-by-column check. It is correct here
only because the loader moves `loaded_at` exactly when a price changes (#4). Verified with the real
loader: re-loading an unchanged price created no version; a revised price closed the old version
at the instant the new one opened, with no gap and no overlap. Two tests hold those invariants for
every key: exactly one current version, and versions that meet end to end.

**Clock fix.** dbt-postgres stamps snapshots with `timestamp without time zone` while `loaded_at`
is `timestamptz`; dbt warned on every run, and the session time zone would silently decide how
the two compared. A one-macro override keeps the snapshot clock time-zone aware, like every other
timestamp in the project.

**Trade-off, shown by test:** a snapshot only sees what exists when it runs. A price revised twice
between runs (RM2.15 to 2.25 to 2.35) recorded 2.15 and 2.35; the 2.25 was never seen. dbt is run by
hand today. The fix is to run `dbt snapshot` right after each fuel ingestion, from Dagster.

### 49. The lineage diagram is generated from the manifest and checked in CI

A screenshot of `dbt docs serve` goes stale the first time a model changes. `col_pipeline.lineage`
reads `dbt/target/manifest.json` and writes `docs/lineage.svg`: one column per layer, nodes ordered
to keep lines short, an arrowhead only where a line really ends. Output is deterministic (verified
byte-identical on regeneration), so CI runs `tasks.py lineage --check` and fails a pull request
whose diagram does not match its models. Standard library only. **Found while building it:** with
seeds in their own column between raw and staging, every raw-to-staging line passed behind a seed
and looked like it flowed through it; seeds moved to the bottom of the staging column.
**Trade-off:** a hand-rolled layout is simpler than the dbt docs graph and does not scale to
hundreds of models; the interactive docs site (`tasks.py dbt-docs`) remains the full view.

### 50. Descriptions are persisted to Postgres as comments

`persist_docs` writes every model and column description into the database as a table or column
comment, so an analyst in DBeaver or psql sees the same documentation as the docs site, including
the grain and the partial-month rule. Verified on tables, views and columns. **Trade-off:** each
build issues extra `COMMENT ON` statements, which is negligible here.

---

## Orchestrating the warehouse

### 51. Dagster runs dbt: ingestion and the warehouse are one asset graph

**Context.** dbt ran only when someone typed `dbt-build`. Two consequences: the snapshot missed
revisions made between runs (#48), and, more importantly, the marts a dashboard reads went stale
while every freshness check on `raw` stayed green.

**Decision.** `dagster-dbt` turns every dbt model, seed and snapshot into a Dagster asset and every
dbt test into an asset check. A custom translator maps dbt's sources onto the ingestion assets that
write them (`raw.fuel_price_weekly` is `fuel_prices_raw`), so the graph is one piece from the API
call to `marts/fct_fuel_vs_transport_cpi`. Each ingestion job keeps its name and schedule but now
selects its source plus everything downstream, so the warehouse refreshes in the same run, coupled
by data rather than by the clock. `dbt_build_job` (`tasks.py materialize warehouse`) builds the
whole warehouse, needed once on a fresh database where seeds do not exist yet.

**Details that mattered.**
- dbt uses the pipeline resource's own database URL, set only while the dbt process starts, so
  dbt and the Python loader cannot point at different databases.
- The code location is lazy (`@dg.definitions`), so importing the module never needs a dbt
  manifest; the Dagster tasks in `tasks.py` run `dbt parse` first. Without this, every existing
  orchestration test failed on machines without dbt.
- `dagster-dbt` pins Dagster exactly, so the two always move together; it supports dbt-core below
  1.13 through an extra this project does not install, so that bound is now stated in our own
  `pyproject.toml`.
- No retry on the dbt step: a failed dbt test is deterministic (#20).

**Tested** with real Dagster, dbt and Postgres: a revised RON95 price went through gate, load,
staging, the snapshot (two versions, old one closed) and the mart in one run, and a failing dbt
test failed the run. With the downstream selection removed, the test fails. A first version of the
test passed once and then failed on a re-run, because a snapshot is cumulative and the fixture only
emptied `raw`; each test now starts from an empty warehouse.

**Trade-off.** A dbt failure now marks the ingestion run failed even though the data loaded
correctly; the raw load is kept, and the dbt assets can be re-run alone from the UI. The dbt step
adds about 10 seconds to a fuel run (measured on the sample data: 6.5 s inside dbt, the rest dbt
starting up; real history is larger). The definitions also need a manifest to load.

### 52. Dagster, not Airflow (recorded after the fact)

The roadmap's Tier 2 names Airflow; Phase 2 chose Dagster, and the reason was not written down at
the time. This entry reconstructs the case from how the project actually uses it.

**For Dagster here.** It installs with pip and runs natively on Windows, where this project is
developed and where CI validates it; Airflow needs Docker or WSL2 on Windows. Local setup is one
command (`tasks.py dagster`) instead of a scheduler, webserver and metadata database. Blocking
asset checks are what implement the quality gate (#21). Its asset model describes data, so "what
is stale" and "refresh everything downstream" (#51) are built in.

**For Airflow.** It is far more common in job descriptions; managed services (AWS MWAA, Google
Cloud Composer) are Airflow, and the roadmap's Tier 5 uses them; its ecosystem is larger. The core
ideas transfer directly: schedules, retries, idempotent tasks, backfills, sensors (README table).

**Plan.** Keep Dagster here. Learn Airflow hands-on in Tier 5 by rebuilding a pipeline on managed
Airflow, where it is the natural tool. **Lesson:** write the reason down when the choice is made;
reconstructing it later is weaker evidence than a decision recorded at the time.
