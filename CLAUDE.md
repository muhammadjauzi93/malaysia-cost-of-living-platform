# Working instructions for Claude Code

This repository is a portfolio data engineering project: Malaysian fuel prices, state CPI and
exchange rates, from public sources, to a dashboard. Phases 1 to 3 are built. **Phase 4 (the
dashboard) is the current work.** Start every session by reading `docs/PHASE4-HANDOVER.md`.

## How the owner works

- **Plan before changing code.** For anything beyond a small fix, write the plan, name the
  trade-offs, and wait for a yes before editing. The owner decides; you propose.
- **The owner reviews everything.** Commits are small and described in the commit message. Do not
  push, merge, tag or open pull requests. Prepare them and say what to run.
- **Say what was verified and what was not.** The sandbox is not the owner's machine. Real
  data, Windows and GitHub CI have not been run by you unless the owner reports the output.
- **Explain decisions in DECISIONS.md** with the reason and the trade-off, the way entries #1 to
  #54 do. Record a decision when it is made, not later.
- **Never silence a warning or a failing check** by recording an event, adding an exception or
  lowering a threshold without the owner's explicit decision. The seed `fuel_price_events.csv`
  is for reviewed real events only.

## Commands the owner runs (Windows PowerShell)

Use the `python tasks.py ...` commands from the README. Do not use `uv run` to verify behaviour:
it puts the virtual environment on PATH, which hid a real Windows bug (DECISIONS #51).

| Task | Command |
|---|---|
| Lint | `python tasks.py lint` |
| All tests (needs Postgres on port 5433) | `python tasks.py test` |
| Warehouse build and tests | `python tasks.py dbt-build` |
| Freshness | `python tasks.py dbt-freshness` |
| Lineage diagram check | `python tasks.py lineage --check` |
| Dagster validate | `python tasks.py validate` |
| Demo data (scratch database only) | `python tasks.py reset-db --yes` then `python tasks.py load-sample` |

Postgres runs in Docker: `python tasks.py up`. Credentials live in `.env`, which is never
committed and never printed.

## Rules for code in this repository

- **The dashboard reads only the `marts` schema.** Never query `raw`, `staging`,
  `intermediate`, `snapshots` or `reference` from the dashboard.
- **Do not change dbt models, the pipeline or the quality rules in Phase 4** unless the plan
  says so and the owner agrees. If a dashboard need looks like a model change, stop and ask.
- **Every new module has tests.** Lint must pass with `python tasks.py lint`.
- **Keep CI green on all three paths**: uv, the Windows job, and the plain-pip fallback. A new
  dependency must be checked on the pip path too (DECISIONS #54).
- **Regenerate `docs/lineage.svg`** after any model change (`python tasks.py lineage`), because CI
  checks it.
- Commit as the owner's configured git identity. Do not add `Co-authored-by` lines.

## Do not

- Contact the data publisher, or record reviews of the open real-data runs. The owner has
  decided these stay open (DECISIONS #53a).
- Print, log or commit secrets, the `.env` file or anything under `data/`.
- Hide the synthetic-data label: rows from `load-sample` carry `source = 'synthetic-sample'` and
  the dashboard must not present them as real.
