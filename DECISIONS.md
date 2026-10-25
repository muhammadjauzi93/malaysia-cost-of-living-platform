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
makes the overlap harmless. Configurable with `LOOKBACK_DAYS`.

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
`runs`, `check`. `python -m col_pipeline.main` still works.

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
