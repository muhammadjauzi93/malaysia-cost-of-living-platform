"""Everything Dagster loads: `dagster dev -m col_pipeline.orchestration.definitions`."""

from __future__ import annotations

from collections.abc import Mapping

import dagster as dg

from col_pipeline.orchestration.alerts import alert_on_failure
from col_pipeline.orchestration.assets import SourceAssets, build_source_assets
from col_pipeline.orchestration.resources import PipelineResource
from col_pipeline.orchestration.schedules import build_monitor_schedule, build_schedules
from col_pipeline.sources import SOURCES
from col_pipeline.sources.base import Source

MONITOR_JOB = "quality_monitor_job"


def build_definitions(
    sources: Mapping[str, Source] | None = None,
    pipeline: PipelineResource | None = None,
    **asset_options: dg.RetryPolicy,
) -> dg.Definitions:
    """Assemble the code location. Tests pass fake sources and a throwaway database.

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

    return dg.Definitions(
        assets=[a for b in built for a in (b.landed, b.raw)],
        asset_checks=[b.freshness for b in built],
        jobs=[*jobs.values(), monitor],
        schedules=[*build_schedules(jobs), build_monitor_schedule(monitor)],
        sensors=[alert_on_failure],
        resources={"pipeline": pipeline or PipelineResource()},
    )


defs = build_definitions()
