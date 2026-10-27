# 🇲🇾 Malaysia Cost-of-Living Data Platform

![CI](https://github.com/muhammadjauzi93/malaysia-cost-of-living-platform/actions/workflows/ci.yml/badge.svg)

An end-to-end data platform that tracks Malaysian fuel prices, inflation and the ringgit,
built to answer one question: **how do pump prices flow through to the cost of living, and
which states feel it most?**

> **Status: Phase 2 of 4.** Three public sources are ingested into Postgres on a schedule by
> Dagster, behind a blocking data-quality gate, with freshness monitoring and failure alerts.
> See the [roadmap](#roadmap).

## Architecture

![Architecture: sources, Dagster orchestration, Postgres, and planned phases](docs/architecture.svg)

## The sources

| Source | API | Lands in | Grain | Runs (KL time) |
|---|---|---|---|---|
| `fuel_prices` | data.gov.my `fuelprice` | `raw.fuel_price_weekly` | date, fuel type | Thu and Fri 10:00 |
| `cpi_state` | OpenDOSM `cpi_state` | `raw.cpi_state_monthly` | state, month, COICOP division | 14:30 on the 19th to 25th |
| `fx_rates` | BNM `/exchange-rate` | `raw.fx_rate_daily` | date, currency, BNM session | Mon to Fri 19:00 |

Each schedule follows its source's publishing rhythm; [DECISIONS.md](DECISIONS.md) #23 explains
each choice. Running a job more often than necessary is harmless, because every load is idempotent.

## How one run works

Every source runs the same three steps. The CLI chains them; Dagster runs them as separate,
observable steps. Both call the same functions in `col_pipeline.runner`.

1. **Land.** Call the API and write the untouched response to `data/landing/<dataset>/`. A row
   in `ops.pipeline_runs` records the attempt.
2. **Gate.** Transform the landed file and judge it. **ERROR** rules (columns, nulls, unique keys,
   plausible ranges, minimum volume, `buying <= selling`) block the load. **WARN** rules (a missing
   currency, an incomplete CPI month) are reported but never block.
3. **Load.** Stage-then-merge upsert. Unchanged rows are not rewritten, so `loaded_at` only moves
   when a value really changed. The run row is closed with exact inserted/updated counts.

After loading, a **freshness** check asks whether the source itself has stalled:

| Table | Newest date may be at most |
|---|---|
| `raw.fuel_price_weekly` | 10 days old |
| `raw.cpi_state_monthly` | 90 days old (month M is published around the 19th of M+1) |
| `raw.fx_rate_daily` | 5 days old (long weekend plus a holiday) |

A daily `quality_monitor_job` (09:00) runs the same checks even when no ingestion happened,
so a stopped schedule cannot hide a stale table.

## Run it

You need **Python 3.11+** and **Docker**. Every command is the same in PowerShell and bash.

```bash
python tasks.py setup              # creates .env and .venv (uses uv if installed)
# edit .env: change POSTGRES_PASSWORD (before the first `up`)
python tasks.py up                 # Postgres on localhost:5433
python tasks.py check              # config, connection, schema
python tasks.py probe-bnm          # see what the BNM API returns today (recommended first)
python tasks.py run                # load all three sources once
python tasks.py runs               # what happened
```

Then start the orchestrator:

```bash
python tasks.py dagster            # UI and scheduler at http://localhost:3000 (Ctrl+C stops)
```

The schedules and the failure sensor are switched on by default. Dagster keeps its state in
`dagster_home/`, so schedules survive a restart.

### Upgrading from Phase 1

* Run `python tasks.py init-db` once. Docker only creates tables on a brand-new volume; this
  adds the two new tables to your existing database. It is safe to repeat.
* `LOOKBACK_DAYS` is gone from `.env`: each source now has its own lookback.
* New runs are logged under source names (`fuel_prices`, not `fuel_price_weekly`).

### All tasks

```
python tasks.py                       # lists everything
python tasks.py run fx_rates          # one source
python tasks.py run --since 2026-10-01   # backfill (idempotent: safe to repeat)
python tasks.py full-refresh cpi_state   # re-pull a source's full history
python tasks.py materialize fuel_prices  # run a Dagster job once, without the UI
python tasks.py monitor               # freshness checks only; fails if anything is stale
python tasks.py validate              # can Dagster load every definition?
python tasks.py sources | init-db | check | probe-bnm
python tasks.py test | lint | fmt
python tasks.py psql | reset-db | down
```

### Alerts

Set `SLACK_WEBHOOK_URL` in `.env` to get a Slack message whenever a Dagster run fails: a blocked
quality gate, a stale table, or an API that stayed down after retries. Without it, alerts go to
the Dagster log. Treat the webhook URL as a secret.

### Without uv

`python tasks.py setup` notices uv is missing (or pass `--pip`) and uses `venv` plus `pip`. You
never need to activate the venv: `tasks.py` finds it. By hand:

| Step | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| Create venv | `py -m venv .venv` | `python3 -m venv .venv` |
| Activate | `.venv\Scripts\Activate.ps1` | `source .venv/bin/activate` |
| Install | `python -m pip install -U pip` then `pip install -e . --group dev` | same |
| Run | `col-pipeline run` | same |

### If something goes wrong

* `python tasks.py check` says whether it is configuration, a stopped Postgres, or missing tables.
* A BNM problem? `python tasks.py probe-bnm` saves BNM's exact responses to `data/probe/`.
* A run stuck at `running` in `runs`? The process died. The next run of that source closes it
  as abandoned after six hours.

## Proof it works

```bash
python tasks.py run && python tasks.py run && python tasks.py runs
```

The second run reports `0 inserted, 0 updated` for every source. The test suite proves the rest
against a real Postgres, on Python 3.11 to 3.13:

* **Idempotency** for all three sources, through the CLI and through Dagster.
* **The gate blocks**: a price of RM215 (sen instead of ringgit) or a CPI of 1324 loads nothing,
  not even the good rows, and the run is recorded as failed with the reason.
* **Retries are real and audited**: a flaky API succeeds on the third attempt, with two failed
  and one successful row in the run log. A failed gate is *not* retried: bad data cannot be fixed
  by trying again.
* **Real HTTP**: a local fake API server checks the headers and query strings that actually go
  over the wire, a genuine 503 followed by a retry, and a BNM holiday 404 during a backfill.

## Tests

```bash
python tasks.py test                    # unit tests, no database needed
```

Integration tests run when `TEST_DB_URL` points at a **disposable** database, because they
truncate tables. CI runs Linux on 3.11 to 3.13 with Postgres, Windows with uv, and the no-uv pip
path on both, and checks that Dagster loads every definition.

## Known limitations

* **BNM's per-date endpoint is inferred.** The daily path uses BNM's documented "latest rates"
  endpoint. Backfills (`--since`) call `exchange-rate/{currency}/date/{date}`, a path taken from
  secondary sources. If a backfill finds nothing, the log says so and points at `probe-bnm`.
* **Schedule times are reasoned, not confirmed.** See DECISIONS.md #23.
* **Dagster runs on the host** with `dagster dev`, not in a container. See #26.

## Design decisions

Every non-obvious choice is written down with its trade-off in [DECISIONS.md](DECISIONS.md):
Phase 1 (#1 to #7), the portability revision (#8 to #13) and Phase 2 (#14 to #28).

## Roadmap

- [x] **Phase 1** Fuel prices to Postgres. Idempotent, incremental, logged, tested; runs on Linux and Windows, Python 3.11 to 3.13.
- [x] **Phase 2** CPI and exchange rates. Dagster schedules, retries, blocking quality gate, freshness monitoring, alerts.
- [ ] **Phase 3** dbt staging, intermediate and marts; snapshots; docs; dbt in CI.
- [ ] **Phase 4** Dashboard, metrics, write-up.

## Data sources

* Fuel prices: [data.gov.my](https://data.gov.my/data-catalogue/fuelprice), CC BY 4.0.
* CPI by state: [OpenDOSM](https://open.dosm.gov.my/data-catalogue/cpi_state), CC BY 4.0.
* Exchange rates: [Bank Negara Malaysia Open API](https://api.bnm.gov.my); see BNM's terms of use.
