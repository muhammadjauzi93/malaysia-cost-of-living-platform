# Design decisions

Short records of choices made, the alternatives, and what was traded away.
Newest at the bottom. Phase 1 = fuel price ingestion.

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
