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
first time a new fuel appears. Monthly averages carry `weekly_observations`: a full month has four
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
shows gaps of more than two weeks (20 rows in the first real run; which fuel types is not yet
known), and a count would label any such old month partial. Months without published CPI are a separate status, `awaiting_cpi`, because a month
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
