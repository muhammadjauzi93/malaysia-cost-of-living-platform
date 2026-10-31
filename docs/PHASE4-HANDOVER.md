# Phase 4 handover: the dashboard, the README numbers and the launch

Read this before changing anything. `CLAUDE.md` at the repo root holds the working rules; this
file holds the state, the decisions still open, and the plan.

## 1. Where things stand

**Built and tested on the synthetic sample** (not yet on the owner's full real history):

- Ingestion of three public sources into Postgres: fuel prices (data.gov.my), state CPI
  (OpenDOSM), exchange rates (BNM). Idempotent, with a blocking quality gate and freshness checks.
- Dagster orchestration, with each dbt model, seed and snapshot as an asset and each dbt test as
  an asset check. Ingestion jobs refresh the warehouse downstream of their source in the same run.
- A dbt warehouse: 3 staging views, 5 intermediate views, 9 marts (5 dimensions, 4 facts), 4 seeds
  (states, CPI divisions, currencies, reviewed fuel events), 1 snapshot of fuel prices.
  On the sample build: 217 nodes, all passing (189 data tests, 6 unit tests, 11 incremental
  re-run checks in CI).
- A lineage diagram generated from the dbt manifest and checked in CI (`docs/lineage.svg`).
- DECISIONS.md, entries #1 to #54.

**Git state (verify this first, it changes):**

- `main` is at the snapshot merge (PR #6), tagged `v0.3.0`.
- PR #7 (`dagster-dbt`) is open with the Dagster integration, the identical-price rule and the
  Phase 3 closing commit. Its CI was failing on the plain-pip job because of an unpinned gRPC
  package; the fix is a two-commit bundle the owner applies to that branch (DECISIONS #54).
- Phase 4 starts from `main` **after PR #7 is merged and the fix is in**. If that has not happened,
  ask the owner before branching, and say which base you used.
- Planned tag after PR #7 merges: `v0.3.1` (Phase 3 closed, with Dagster and the rule).

**Real-data state (owner's machine, reported by the owner):**

- The owner ran `python tasks.py materialize fuel_prices` successfully: ingestion, gate, load,
  dbt models, snapshot and mart in one Dagster run.
- The identical-price rule reports three runs, none reviewed (DECISIONS #53a):
  1. `diesel` = `diesel_eastmsia`, 30 Mar 2017 to 6 Jun 2024 (359 announcements). Unchecked.
  2. `diesel_eastmsia` = `diesel_skds`, 10 Jun 2024 to 24 Sep 2026 (121). Unchecked.
  3. `diesel` = `diesel_eastmsia`, 1 to 29 Oct 2026 (5). The incident. Not contacted publisher.
- The owner has decided: **no reviews are recorded and the publisher is not contacted** while
  these stay open. Do not change that. The rule fails on real data by design.
- The review check matches a run by its start date only, so an extended reviewed run passes
  silently. Known gap, not fixed (DECISIONS #53a). Do not treat the rule as complete.

**Not verified** (say so when it matters):

- The Windows CI job, and the pip-fallback job after the gRPC pin.
- The warehouse on the owner's full real history. Only synthetic data has run end to end in CI.
- Whether real CPI data is complete for all 16 states in every month. The mart's CPI status
  column handles missing months, but this has not been observed on real data.

## 2. The goal

The roadmap's Phase 4 criteria, verbatim in intent:

1. The dashboard answers the central question on **one page**, with a screenshot in the README.
2. The README has **measured** key numbers, an architecture diagram, and a run section a stranger
   can follow. Replace placeholders with measured values, not estimates.
3. DECISIONS.md covers every phase, including the revised choices and why.
4. Two interview stories are ready: one thing that broke and how it was found, one trade-off
   chosen on purpose. (Candidates already in the record: the JPY per-100 unit trap, DECISIONS #39;
   the day-weighted fuel price found on real data, #46; the identical-price rule, #53; the PATH
   bug found on Windows, #51.)
5. The repository is pinned on the owner's GitHub profile, tagged `v1.0.0`, with a GitHub Release
   and a launch post.

**The central question:** does a RON95 pump price change show up in transport CPI in the
following month, and which states feel it most?

## 3. Decisions the owner must make first

Do not write dashboard code until these are answered. Each has a recommendation; the owner picks.

**D1. Dashboard tool.** The handover listed three.
- **Streamlit** (Python, runs locally, matches the stack). *Recommended.* Needs `streamlit` and a
  charting library added to `pyproject.toml`, so the pip-fallback job must be checked (#54).
- **Evidence** (SQL and Markdown, static site). Good for a static published demo; a second toolchain.
- **Power BI.** The owner uses it at work. It cannot be run by a reviewer without Power BI, which
  weakens the portfolio claim that a reviewer can see it working in two minutes.

**D2. Where the dashboard reads from.** The dashboard reads the **local** Postgres marts. A hosted
public demo cannot reach a local database, so it needs a snapshot. Options:
- **A: local only**, with a screenshot in the README. *Recommended for v1.0.0.* No hosting cost,
  no data republication.
- **B: a static snapshot** of the marts, exported by a new task (for example `export-marts`) to
  Parquet or CSV, hosted on a free static host. Would publish **synthetic** data only, unless the
  owner decides otherwise.

**D3. Data-health page.** The owner's requirement is that the dashboard shows whether the data can
be trusted, including the open identical-price runs. The dashboard may read only `marts`, and
pipeline runs and rule results are not in any mart today. Options:
- **A: a new mart** `marts.mart_data_health` built from `ops.pipeline_runs` and the freshness
  results. Needs a dbt model change, which Phase 4 does not otherwise make. *Recommended* if the
  page is wanted: it keeps the marts-only rule intact.
- **B: no health page** in v1.0.0; the README states the open issues instead.

**D4. Read-only database role.** Recommended: create a `dashboard_reader` role that can `SELECT`
only from the `marts` schema, and have the app connect as that role. This is a small, standard
protection that also enforces the marts-only rule in the database itself. Owner to approve the
task that creates it.

**D5. dbt docs hosting.** The roadmap offers GitHub Pages. Options: publish the generated docs site
(`python tasks.py dbt-docs`) to Pages, or link the locally generated site from the README only.
Note that the docs site embeds model descriptions, which are public anyway.

**D6. Release scope.** Confirm v1.0.0 means: dashboard, README metrics, lineage, docs, DECISIONS,
and launch posts. Anything beyond that (hosting, API, ML) is out of scope for this phase.

## 4. Scope and constraints

**Pages** (one page must answer the central question; the others support it):

1. **Overview (the answer page):** for a chosen state and period, RON95 monthly average next to
   transport CPI and the month-on-month change. Shows the lag comparison: last month's fuel change
   beside this month's transport CPI change (`fuel_mom_change_prev_month_myr` and
   `transport_cpi_mom_pct`). Data: `marts.fct_fuel_vs_transport_cpi`.
2. **States:** every state side by side for the latest complete month, ranked by transport CPI
   change. Data: same mart.
3. **Fuel series:** RON95 price over time with weekly changes, and event annotations from
   `fct_fuel_prices.event_description`. Data: `marts.fct_fuel_prices`.
4. **Data health** (only if D3 = A): freshness, last load, open identical-price runs.

**Hard rules for the pages:**

- **Partial months are labelled.** `fuel_month_status = 'partial'` must be visible on the chart,
  not only in the table (DECISIONS #42). Never compare a partial month as if complete.
- **Awaiting CPI is shown as awaiting**, not as zero or as a fall. `cpi_status = 'awaiting_cpi'`.
- **Fuel prices are national.** A state's chart must not imply a state-level fuel price
  (DECISIONS #33). Label the fuel line as national.
- **Synthetic data is labelled** wherever the database holds it, using the `source` column.
- **The headline fuel is read from the mart**, not hard-coded in the app. It is RON95 by default
  (DECISIONS #41).
- **Diesel is not on the central page.** The open diesel issue affects diesel only; the central
  question is about RON95. Say so on the page if diesel appears anywhere.

**Non-goals for Phase 4:** changing ingestion or dbt models (unless D3 = A is chosen, and then
only the one new mart); fixing the identical-price rule's review gap; contacting the publisher;
recording reviews; forecasting.

## 5. Plan, in steps

Each step is one reviewable commit or one small set of commits. Stop after each step and wait for
the owner.

**Step 4.0: decisions.** Owner answers D1 to D6. Record the answers as DECISIONS #55 to #60 before
code. Output: a short plan for step 4.1.

**Step 4.1: skeleton.** The app starts, connects through the read-only role (D4), and shows the
row count of `fct_fuel_vs_transport_cpi`. Tests: the connection is read-only (an attempted write
fails), and the app refuses to query a non-`marts` schema. Dependencies checked on all three CI
paths, including pip (#54).

**Step 4.2: the overview page.** Built against the mart, with partial and awaiting labels and the
national-fuel label. Tests: query functions return the mart's rows; the lag column matches a
hand-computed value for one state-month; labels appear for a partial month and an awaiting-CPI
month, using the sample data. Screenshot taken.

**Step 4.3: the states and fuel-series pages.** Same test standard. The states ranking must not
rank an awaiting month as a fall.

**Step 4.4: data health** (only if D3 = A). The new mart, its dbt tests and its unit tests,
`docs/lineage.svg` regenerated, DECISIONS entry. Then the page.

**Step 4.5: README metrics, measured.** Each number comes from a command the README shows: sources
(3), models, seeds, snapshot, tests (from `dbt build`), rows processed (from `ops.pipeline_runs`
or `dbt` output), run times (from the owner's own run, not estimates), and cost per month, which
is RM0 only if option D2-A is chosen and nothing is hosted. Replace every placeholder. Include the
architecture diagram and the lineage diagram.

**Step 4.6: demo path.** One documented sequence a stranger can run: `python tasks.py up`,
`python tasks.py reset-db --yes`, `python tasks.py load-sample`, `python tasks.py dbt-build`, the
dashboard command. Test it on a clean clone, the way earlier bundles were rehearsed.

**Step 4.7: docs and lineage.** Publish or link the dbt docs (D5). Confirm `lineage --check`
passes.

**Step 4.8: DECISIONS and interview notes.** Phase 4 entries for each choice made, with trade-offs.
Two prepared stories, in the owner's words, written in DECISIONS or a notes file the owner
chooses.

**Step 4.9: release.** Owner merges, tags `v1.0.0`, creates the GitHub Release. Claude drafts the
release text and three launch posts for the owner to edit; Claude does not post anything.

## 6. Acceptance checklist

- [ ] One page answers the central question, with a screenshot in the README.
- [ ] Partial months, awaiting CPI, national fuel and synthetic data are all labelled.
- [ ] The dashboard reads only `marts`, enforced by the read-only role, and a test checks it.
- [ ] README numbers are measured, each traceable to a command.
- [ ] `python tasks.py lint`, all tests, `dbt-build`, `lineage --check` pass on the uv path and on
      the pip path.
- [ ] CI is green on the Windows job (owner confirms).
- [ ] DECISIONS covers Phase 4, including what was not done and why.
- [ ] Owner has merged, tagged `v1.0.0`, and published the release.

## 7. Known issues that must appear in the README, not be hidden

- The identical-price rule fails on the owner's real data (three runs; DECISIONS #53a).
- The review check matches by start date only (DECISIONS #53a).
- Dagster runs dbt only when it is running; schedules fire only while `python tasks.py dagster`
  is up (DECISIONS #26).
- The snapshot only sees a revision that exists when the pipeline runs (DECISIONS #48).

## 8. Commands and environment

- Windows PowerShell, from the repo root. Use `python tasks.py ...` as in the README.
- Do not use `uv run` to verify behaviour (DECISIONS #51).
- The database is Docker Postgres on **port 5433** (`python tasks.py up`). Credentials are in
  `.env`. Never print them.
- Scratch and demo work uses a scratch database. `load-sample` refuses to run on a database that
  already holds rows; that safety rail is deliberate.

## 9. First session checklist

1. `git status` and `git branch --show-current`. Confirm the base (see section 1).
2. Read `CLAUDE.md` and this file.
3. Ask the owner D1 to D6 (section 3). Do not start step 4.1 before they are answered.
4. Run `python tasks.py lint` and `python tasks.py test` on the owner's machine and report the
   output before changing anything, so the starting point is known.
