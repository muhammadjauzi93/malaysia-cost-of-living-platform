# Design decisions

Short records of choices made, the alternatives, and what was traded away.
Newest at the bottom. Phase 1 = fuel price ingestion, Phase 2 = orchestration and quality,
Phase 3 = the dbt warehouse (#30 onwards), Phase 4 = the dashboard and release (#55 onwards).

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
- **Found on the first real run, on Windows:** dagster-dbt looks for a program called `dbt` on
  PATH. `python tasks.py` starts the venv's Python directly without activating the venv, so the
  folder holding `dbt.exe` was not on PATH and the run stopped before doing anything. Every test
  and CI step had passed because they all used `uv run`, which puts that folder on PATH. The
  resource now points at the `dbt` program beside the running Python, a regression test empties
  PATH and builds it, and CI's `validate` steps run plain `python tasks.py`, as the README does.
  **Lesson:** test the command the way users run it, not the way it is convenient to run in CI.

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

### 53. A copied price series fails a quality rule, unless it has been reviewed

**Found on real data.** From 1 October 2026, `diesel_eastmsia` carried exactly the peninsular
`diesel` price every week: RM5.27, 5.27, 5.17, 5.07 and 5.27. The range check (RM0.50 to RM10.00)
accepted every value, and the week-on-week warning caught only the first jump from RM2.15. Nothing
caught the repetition itself. The pipeline recorded what the publisher returned, and the snapshot
kept the earlier RM2.15 version, so the history is traceable.

**Decision.** `int_fuel_identical_runs` lists every run of consecutive shared announcements in which
two fuel types carry the same price. A singular test, `assert_no_unreviewed_identical_fuel_runs`,
fails for any run of at least three announcements whose first date has no reviewed event in
`fuel_price_events.csv` for either fuel or for every fuel. The threshold is the variable
`identical_run_min_announcements` (default 3).

**Why a reviewed event, not a plain rule.** Two fuels were legitimately identical in 2018: diesel
and East Malaysia diesel were both RM2.18 for five announcements while prices were frozen. That is
already in the data, so a rule with no exception fails on history. The 2018 freeze starts on a date
already reviewed as a schedule change, so the run is accepted. The same mechanism applies to any
future legitimate match, and the review stays in version control.

**Severity: error, but it does not stop the dashboard.** A failing dbt test fails the Dagster run,
which sends the failure alert. The assets still materialise before checks run, so the mart refreshes.
An end-to-end test proves both: the run fails, and `marts/fct_fuel_vs_transport_cpi` is rebuilt in
the same run. A copied series is not published as a real value, but one bad series must not freeze
the dashboard.

**What it was tested against.**
- Clean synthetic sample: the rule stays silent (217 of 217).
- The incident reproduced: exactly one run is found, starting 15 October with three announcements.
- Unit tests on the model: two identical runs separated by a different price are two runs; a
  reviewed 2018 run is marked reviewed and an unreviewed 2026 run is not.
- Sabotage: with the threshold set to 99, the end-to-end test fails with no check failing, so it
  does test the rule.

**Trade-offs.**
- **A legitimate identical price will alert** until someone reviews it. That is the cost of a rule
  that does not depend on knowing the answer in advance.
- **A missing announcement does not break a run.** The first version of the model merged a 2018
  run with a 2026 run, because nothing differed between them. A stretch where one fuel has no
  announcements counts as consecutive. Real history has many differing weeks between the two, so
  this does not affect current results, but it is a choice, and the unit tests record it.
- **The first build on your real data may show other historical runs** we have not seen. Each one
  needs the same decision: a real freeze gets a reviewed event; an error goes to the publisher.
- **The range check is still too wide for this.** A per-fuel band would not have caught this
  either: diesel itself trades around RM5.27, so the value sits inside a sensible band. Only the
  repetition gives it away, which is why this is a separate rule. Narrower bands are a later
  improvement.

**Action for the publisher.** Report the `diesel_eastmsia` values for the weeks of 1 to 29 October
2026 to data.gov.my, with the table from this decision as evidence.

### 53a. Status at Phase 3 close

**Real-data findings, not reviewed.** The rule reports three runs on the real data:

1. `diesel` and `diesel_eastmsia` identical from 30 March 2017 to 6 June 2024 (359 announcements).
   A seven-year match is either a genuine shared price or a copied column, and it has not been
   checked against published weeks. The June 2024 event in the seed (East Malaysia diesel did not
   take the peninsular jump) is consistent with the run ending on 6 June.
2. `diesel_eastmsia` and `diesel_skds` identical from 10 June 2024 to 24 September 2026 (121
   announcements). Possibly two subsidy schemes sharing a price; not checked against the scheme
   definitions.
3. `diesel` and `diesel_eastmsia` identical from 1 October to 29 October 2026 (5 announcements).
   The incident in #53. For two years `diesel_eastmsia` followed `diesel_skds`; from 1 October it
   follows peninsular `diesel`. A change of matching partner is the clearest sign of a column
   problem rather than a market move.

No reviewed events were recorded for these runs, and no report was made to the publisher. The
rule fails until someone resolves them, which is the intended behaviour of an unreviewed run.

**Known gap, not fixed.** The review check matches a run by its start date. If a reviewed run
later grows, the new announcements are accepted silently, because the start date does not change.
Run 1 shows the same problem in miniature: 2018 is inside a run that starts in 2017. The fix is a
review that covers a span (a fuel pair, a start date and a reviewed end date), so any extension
fails until it is reviewed. It was not built, because it changes what a review means and that
change was not agreed for this phase. It should be the first change to the rule when work resumes.

**Phase 3 scope at close.** The warehouse (staging, intermediate, marts, snapshot, lineage,
persisted docs), its checks, and its orchestration in Dagster are complete and tested on the
synthetic sample. The real-data findings above are open and documented; the rule is a working
detector, not a resolved data-quality question.

### 54. The plain-pip install path is tested on its own, and it pins the transitive gRPC package

**Found by the CI pip-fallback job, which failed on Ubuntu and Windows while every uv job passed.**
Plain pip has no lockfile, so it resolved the newest `grpcio-health-checking` (1.85.1). That release
is generated against protobuf 7, while the stack runs protobuf 6.33.6. Importing dagster then fails
with a protobuf version error, so the whole orchestration test module could not be collected.

**Decision.** State the bound the lockfile already uses: `grpcio-health-checking>=1.81.1,<1.82`. The
uv lock gains two metadata lines and no pinned version changes. Upgrading to protobuf 7 instead was
rejected: it is shared with the dbt packages and is a larger change than this failure justifies.

**What this shows.** A lockfile hides resolver drift. Every uv job tested exact versions, so the
newest-version problem could not appear there. The pip job is the only one that sees what a user
who does not use uv gets, and it must stay green.

**Two mistakes made while diagnosing it, recorded because they would repeat.**
- My first reproduction installed only the dev group. CI's `setup --pip` installs the dbt group too,
  and the missing `dbt` program produced a failure that was not the real one. A reproduction must
  match the CI command exactly, including every group.
- Protobuf's error names the gencode version, which points at the cause directly. Reading the
  error before guessing saved a change to the wrong package.

**Not verified.** The Windows pip job runs on GitHub and has not been run here. The Ubuntu and
Windows pip environments resolve the same packages, so the result should match, but the CI run is
the test.

---

## Phase 4: the dashboard and the release

Entries 55 to 60 are the owner's answers to the six open questions in `docs/PHASE4-HANDOVER.md`
(D1 to D6), recorded on 31 Oct 2026 before any dashboard code was written.

### 55. The dashboard is Streamlit (D1)

**Decision.** Build the dashboard in Streamlit, in Python, inside this repository.

**Why.** It is the same language and toolchain as the pipeline, so the query functions are tested
with the same pytest suite and the same CI jobs. A reviewer runs it with one command after the
demo data is loaded.

**Alternatives rejected.** Evidence builds a good static site, but it adds a Node toolchain to a
Python project. Power BI is the owner's work tool, but a reviewer without it cannot open the
dashboard, which defeats the point of a portfolio project you can run.

**Trade-off.** Streamlit and a charting library become runtime dependencies. They are resolved
without a lockfile on the pip path, so that job must be checked when they are added (#54).

### 56. The dashboard reads the local Postgres marts; nothing is hosted in v1.0.0 (D2)

**Decision.** The app connects to the local Docker Postgres and reads the `marts` schema. The
README shows a screenshot. No public demo is hosted.

**Why.** A hosted app cannot reach a local database, so a public demo would need an exported
snapshot of the marts, a host, and a decision on which data to republish. None of that is needed
to answer the central question, and hosting nothing keeps the running cost at RM0.

**Trade-off.** A visitor sees a screenshot, not a live page, unless they clone and run it. A
static snapshot export stays a possible later step, and it would publish synthetic data only
unless the owner decides otherwise.

### 57. Data health gets its own mart, the one dbt change in Phase 4 (D3)

**Decision.** Add `marts.mart_data_health`, built from pipeline metadata (`ops.pipeline_runs` and
the freshness and rule results), and a dashboard page that reads it.

**Why.** The owner wants the dashboard to show whether the data can be trusted, including the open
identical-price runs (#53a). The dashboard may read only `marts`, so reading `ops` directly would
break that rule. A mart keeps the rule, and the data gets dbt tests like every other mart.

**Trade-off.** Phase 4 otherwise changes no models. This one is allowed on purpose and is limited
to the one mart, its tests and the regenerated lineage diagram. Its exact inputs are settled in
step 4.4: run history is in `ops.pipeline_runs`, but dbt's freshness results are written to a file,
not the database, so how they reach a mart is still open.

### 58. The app connects as a read-only `dashboard_reader` role (D4)

**Decision.** A `tasks.py` task creates a `dashboard_reader` role with `USAGE` on `marts` and
`SELECT` on its tables only. The dashboard connects as that role.

**Why.** The marts-only rule is then enforced by Postgres, not only by code review. A test checks
that a write fails and that reading another schema fails.

**Trade-off.** One more set of credentials in `.env`, and one more setup step in the demo path.
Tables that dbt rebuilds must keep the grant, which step 4.1 has to handle (for example with
default privileges or a dbt grant), and a test must prove it survives a rebuild.

### 59. The dbt docs site is published to GitHub Pages (D5)

**Decision.** Publish the generated dbt docs site to GitHub Pages and link it from the README.

**Why.** A reviewer can click through the models, columns, tests and lineage without cloning
anything. The site holds model descriptions and compiled SQL, which are public in the repository
already.

**Trade-off.** A Pages workflow to write and keep green, and the owner must turn on Pages in the
repository settings. The site must be built from the synthetic sample in CI, never from the
owner's real database. Step 4.7 checks that the published catalog holds no real data.

### 60. v1.0.0 scope (D6)

**Decision.** v1.0.0 is the dashboard, measured README numbers, the lineage diagram, the dbt docs
site, DECISIONS for every phase, and launch posts.

**Out of scope, on purpose.** Hosting the dashboard, an API, forecasting or ML, fixing the
identical-price rule's review gap, and resolving the open real-data runs (#53a). These are listed
in the README as known issues or next steps, not hidden.

### 61. How the read-only role is built and kept (step 4.1)

**Decision.** `python tasks.py create-dashboard-role` creates `dashboard_reader` with `USAGE` on
`marts`, `SELECT` on its tables, and a default privilege so that tables dbt creates there later
are readable too. It is a separate task, safe to repeat, and not part of `sql/schema.sql`.

**Why default privileges, not dbt's `grants` config.** dbt rebuilds every mart as a new table, and
a grant on the old table goes with it. dbt's `grants` would re-apply the grant on every build, but
it fails on any database where the role does not exist, which is CI, a fresh clone, and every
reviewer's first build. It would also change the dbt project, which Phase 4 avoids. The default
privilege is set once, by the user that runs dbt, and survives every rebuild. A test runs
`dbt run` on the mart and reads it again as the role. With the default privilege removed, that
test fails with `permission denied for table fct_fuel_vs_transport_cpi`. That failure was checked
on purpose, so the test is known to catch the problem.

**Why not `schema.sql`.** Docker runs it only on a brand-new volume, and creating the role there
would mean passing a second password into the container. A task also lets a repeat reset the
password from `.env`.

**Read-only three times over.** The role can only `SELECT`. The role also has
`default_transaction_read_only = on`. The app opens every session read-only as well. The
dashboard's code reads through one function that accepts only a bare table name and always
adds the `marts.` prefix, so there is no way to pass SQL or another schema.

**No fallback to the owner's password.** If `DASHBOARD_DB_PASSWORD` is missing, the app shows an
error. Falling back to `POSTGRES_PASSWORD` would make it work while silently bypassing the role.

**Trade-offs.**
- Dropping the `marts` schema also drops its default privilege. A rebuild does not drop the
  schema, but `reset-db` and the Dagster integration tests do, so the task must run again after
  them. The README says so, and in CI the role tests run before the tests that drop schemas.
- Postgres roles belong to the whole server, not one database. So the tests use their own role,
  `dashboard_reader_test`, rather than resetting the real role's password, which would also
  change it for every other database on that server.

**Other choices made here.**
- Charts will use Altair, which Streamlit already installs, so there is no second charting
  dependency.
- `.streamlit/config.toml` turns off Streamlit's usage statistics and its first-run email prompt.
- Adding Streamlit changed no pinned version in `uv.lock`; protobuf stays at 6.33.6 (#54).

**Not verified.** The CI steps have not run on GitHub yet. The dashboard has not been opened in a
browser; a Streamlit test harness ran the script against a scratch warehouse, and the
`dashboard` task's server answered its health check.

### 62. The synthetic-data label comes from a first version of the data-health mart

**Problem.** The dashboard must label synthetic data, and it may read only `marts`. No mart carried
the `source` column, so the label could not be produced without a model change.

**Decision (owner, 31 Oct 2026).** Build `marts.mart_data_health` now, ahead of the data-health
page, rather than adding `source` to existing marts. One row per source, always all three: rows,
synthetic rows, data date range, and the pipeline's last run from `ops.pipeline_runs`, which
becomes a dbt source. The dashboard shows a synthetic-data banner whenever any source has
synthetic rows.

**Why this mart and not a `source` column on the facts.** It changes no existing mart, and it is
the mart #57 already approved. A banner per source is also what a viewer needs; a per-row label
would only matter if real and synthetic rows were mixed, which `load-sample` prevents by refusing
to run on a database that already holds rows.

**What it deliberately leaves out.**
- **The open identical-price runs.** `int_fuel_identical_runs` has a singular test that fails on the
  real data by design (#53a). `dbt build` skips every model downstream of a failing test, so a mart
  reading that model would be skipped on exactly the data that needs the warning. How the runs reach
  the dashboard is decided in step 4.4.
- **A freshness verdict.** dbt writes freshness results to a file, not the database (#57). The mart
  gives the newest data date; the page can show its age, but repeating the limits from
  `_sources.yml` in SQL would make two copies to keep in step. Also step 4.4.

**Trade-off.** `load-sample` writes no pipeline runs, so on the demo data the last-run columns are
empty. That is true, and the page says "never run by the pipeline" rather than hiding it. The
label string `synthetic-sample` now lives in Python and in SQL; a test fails if they differ, and
the mart's unit test fails if the SQL label is changed.

**One orchestration change it forced.** A new dbt source becomes a Dagster dependency, and
`ops.pipeline_runs` appeared as an unexplained asset in the `default` group, which an existing
test caught. It is now declared as an external table in a `pipeline_metadata` group
(`orchestration/dbt.py`). Nothing materialises it; every ingestion job already refreshes the
health mart because the mart reads all three staging models. Tests pin both facts.

### 63. The dashboard: five pages, a computed answer, and labels on the charts themselves

**Decision (owner, 31 Oct 2026).** Five pages with top navigation: the answer, states, fuel prices,
cost of living, and about the data. The handover asked for three; cost of living and about the data
were added because they put the answer in context, and they need no model change.

**The answer is computed, never written.** The first page states one sentence ("In the N months
after RON95 rose, transport CPI changed by X% on average, against Y% after months when the price
held") built from the numbers, with N always shown, a correlation and slope beside it, and the words
"an association across few months, not proof of cause". On the synthetic sample the finding is noise,
and the page says so through the synthetic-data banner; on real data it will say whatever the data
says. Writing a conclusion into the page would have been easier to make impressive and impossible to
trust.

**How the comparison is made.**
- Last month's change in the day-weighted RON95 average (#46) beside this month's transport CPI change.
- A monthly average that moved by less than half a sen counts as no change: day weighting leaves dust
  in the fourth decimal of a price that held all month.
- Months awaiting CPI drop out of every comparison and ranking. They are labelled on the charts, never
  drawn as zero.
- "The average state" is a simple mean of the 16 states. No mart holds the official national CPI, so
  the page never calls it that.

**Code shape.** `analysis.py` holds every calculation as plain pandas, tested against numbers worked
out by hand in `tests/dashboard_fixtures.py`. `charts.py` builds Altair specs, and tests read the specs
to check the labelling rules. Pages are thin scripts. Each mart is read whole through the marts-only
reader and cached for ten minutes; filters run in pandas, so no viewer input ever reaches SQL.

**Two things found by looking at the dashboard in a browser, not by tests.**
- Vega-Lite cannot stretch a stacked (`vconcat`) chart to its container: the main chart collapsed to a
  sliver. Charts that must line up month for month are now separate charts sharing an explicit list of
  months and a fixed y-axis width. A test checks they share the months.
- Category charts with a fixed pixel height dropped every other state label. They now use a height per
  row.
These were seen with headless Microsoft Edge screenshots taken on the owner's machine against a scratch
database. That check is not in CI; the app tests cover behaviour and the specs, not pixels.

**Theme.** Streamlit's bundled financial-dashboard theme, dark, set in `.streamlit/config.toml` with no
custom CSS. Its Google Fonts links were left out so opening the dashboard makes no third-party request,
in line with turning off Streamlit's usage statistics (#61).

**Trade-offs.**
- Five pages are more to keep correct than three. Every page has a render test on hand-checked data and
  one against a real built warehouse as the read-only role.
- Rise months are few (two in the sample), so the per-state sensitivity table is a pointer, and the page
  says so. A longer real history will make it more useful.
- Diesel appears only on the fuel page, behind a warning about its open findings (#53a). It is not part
  of the answer.

### 64. A glossary page, with its text in a tested module

**Decision (owner, 31 Oct 2026).** A sixth page, after About the data, explains every acronym on the
dashboard and what each number means and why it matters to the question, grouped by the page it
appears on, with a search box. The owner supplied the expansions of SKDS (Sistem Kawalan Diesel
Bersubsidi), SKPS (Sistem Kawalan Petrol Bersubsidi) and BUDI95 (the targeted RON95 subsidy
programme under the BUDI MADANI initiative); the repository did not record them before.

**Why a page, not only tooltips.** The pages already carry short `help` text on their metrics, but a
viewer meeting "pp", "COICOP" or "awaiting CPI" for the first time needs the reason as well as the
definition, and a tooltip has no room for it. The glossary repeats the rules the pages follow (day
weighting #46, national fuel #33, partial months #42, the half-sen tolerance, the headline fuel read
from the mart #41) in one place, written for a reader rather than a reviewer.

**Code shape.** The text is plain data in `glossary.py`, and the page only renders it; it reads no
tables. Tests check that every entry is complete, that no term is listed twice, that the pages it
names are the app's pages, and that every upper-case acronym in the pages' and charts' strings and
in the fuel names has an entry. A new page that shows an unexplained acronym fails CI.

**Trade-offs.**
- The definitions are a second copy of what the pages and `analysis.py` do, so they can drift. The
  acronym test catches a new term, not a changed meaning; changing what a number means needs its
  glossary entry changed by hand, and the module's docstring says so.
- Lower-case units (pp, sen, r) and spelled-out phrases cannot be found by the scan; a short fixed
  list in the tests covers the ones on screen today.
