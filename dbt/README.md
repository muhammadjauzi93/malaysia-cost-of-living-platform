# dbt project: `cost_of_living`

Turns the `raw` tables (written by the Python pipeline) into a tested, documented analytics
layer. Run everything through `python tasks.py` from the repository root; it reads `.env` and
tells dbt where the database is.

```bash
python tasks.py dbt-build              # deps (first time) + build every model + run every test
python tasks.py dbt build -s stg_fx_rates+    # one model and everything downstream
python tasks.py dbt-freshness          # source freshness
python tasks.py dbt docs generate      # docs site and lineage graph (target/)
```

## Layers

| Layer | Schema | Materialised as | Job |
|---|---|---|---|
| sources | `raw` (read only) | n/a | Declare the raw tables and their freshness limits |
| `staging/` | `staging` | view | Rename, cast, trim. One model per source table. No joins, no logic |
| `intermediate/` | `intermediate` | view | Joins, windows, pivots, unit normalisation. Analysts do not query these |
| `marts/` | `marts` | table, incremental for facts | What analysts and the dashboard read |
| `snapshots/` | `snapshots` | snapshot | History of values that get revised |

A mart never reads a raw table directly, so a source change touches exactly one staging model.

## Conventions

* Every model has a description that states its **grain** in one sentence, and every column has a
  description. `tests/test_dbt_project.py` enforces this without needing a database.
* Test severity: `error` for a broken contract (keys, nulls, ranges, fixed code lists); `warn`
  for "something new appeared" (a new fuel type, an untracked currency). See DECISIONS.md #35.
* Freshness is measured on the newest **data date**, never on `loaded_at` (DECISIONS.md #30).
* Rates stay exactly as BNM quotes them in staging (JPY per 100). Converting to "ringgit per 1
  unit" is a derivation and belongs in the intermediate layer.
* The password is never in a file here: `profiles.yml` only names environment variables.
