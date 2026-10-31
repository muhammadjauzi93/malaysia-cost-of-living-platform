"""Everything Dagster loads: `dagster dev -m col_pipeline.orchestration.definitions`."""

from __future__ import annotations

from collections.abc import Mapping

import dagster as dg

from col_pipeline.orchestration.alerts import alert_on_failure
from col_pipeline.orchestration.assets import SourceAssets, build_source_assets
from col_pipeline.orchestration.dbt import DBT_BUILD_JOB, RUN_LOG, build_dbt_assets, dbt_resource
from col_pipeline.orchestration.resources import PipelineResource
from col_pipeline.orchestration.schedules import build_monitor_schedule, build_schedules
from col_pipeline.sources import SOURCES
from col_pipeline.sources.base import Source

MONITOR_JOB = "quality_monitor_job"


def build_definitions(
    sources: Mapping[str, Source] | None = None,
    pipeline: PipelineResource | None = None,
    dbt: dg.AssetsDefinition | None = None,
    **asset_options: dg.RetryPolicy,
) -> dg.Definitions:
    """Assemble the code location. Tests pass fake sources and a throwaway database.

    `dbt` is the warehouse multi-asset (orchestration/dbt.py). With it, each ingestion job also
    refreshes every dbt asset downstream of its raw table, in the same run, and `dbt_build_job`
    builds the whole warehouse. Without it (most tests), the ingestion jobs are unchanged.
    `asset_options` forwards `landed_retry` / `raw_retry` to `build_source_assets`.
    """
    built: list[SourceAssets] = [
        build_source_assets(s, **asset_options) for s in (sources or SOURCES).values()
    ]

    # A job that runs only the freshness checks, so staleness is noticed even if ingestion
    # itself stopped running. Blocking checks fail the run, which triggers the alert sensor.
    monitor = dg.define_asset_job(
        MONITOR_JOB,
        selection=dg.AssetSelection.checks(*(key for b in built for key in b.freshness.check_keys)),
        description="Check every raw table for staleness.",
    )
    jobs = {b.source.name: b.job for b in built}
    assets: list[dg.AssetsDefinition | dg.AssetSpec] = [a for b in built for a in (b.landed, b.raw)]
    extra_jobs = []
    resources: dict[str, object] = {"pipeline": pipeline or PipelineResource()}

    if dbt is not None:
        # Same job names, so schedules and `tasks.py materialize` are unchanged. Coupled by data,
        # not by the clock: the warehouse refreshes right after its inputs land (DECISIONS.md #51).
        jobs = {
            b.source.name: dg.define_asset_job(
                b.job.name,
                selection=dg.AssetSelection.assets(b.landed, b.raw)
                | dg.AssetSelection.assets(b.raw).downstream(),
                description=(
                    f"Ingest {b.source.name}, then refresh every dbt model, snapshot and test "
                    "downstream of it."
                ),
            )
            for b in built
        }
        extra_jobs.append(
            dg.define_asset_job(
                DBT_BUILD_JOB,
                selection=dg.AssetSelection.assets(dbt),
                description="Build the whole dbt warehouse: seeds, models, snapshot and tests.",
            )
        )
        assets.extend([dbt, RUN_LOG])
        resources["dbt"] = dbt_resource()

    return dg.Definitions(
        assets=assets,
        asset_checks=[b.freshness for b in built],
        jobs=[*jobs.values(), monitor, *extra_jobs],
        schedules=[*build_schedules(jobs), build_monitor_schedule(monitor)],
        sensors=[alert_on_failure],
        resources=resources,
    )


def load_definitions() -> dg.Definitions:
    """The production code location: ingestion plus the dbt warehouse."""
    return build_definitions(dbt=build_dbt_assets())


# Lazy: built when Dagster loads the code location, not when this module is imported. Tests
# import build_definitions from here on machines with no dbt manifest, which must keep working.
@dg.definitions
def defs() -> dg.Definitions:
    return load_definitions()
