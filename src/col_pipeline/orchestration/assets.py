"""Dagster assets, generated from `Source` definitions.

Each source becomes two assets and three checks:

    <source>_landed   call the API, land the raw JSON, run the quality gate
        check quality_gate       BLOCKING. ERROR-level rules; a failure stops the run here
        check quality_warnings   WARN-level rules; reported, never blocks
    <source>_raw      load the batch into Postgres (only runs if the gate passed)
        check freshness          has the source stalled? (also run daily on its own)

The assets contain no business logic. They call `col_pipeline.runner`, the same functions
the CLI uses, so the two paths cannot drift apart.

(No `from __future__ import annotations` in this module: Dagster reads parameter annotations
such as `config: IngestConfig` at definition time and cannot resolve them as strings.)
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

import dagster as dg
from sqlalchemy import text

from col_pipeline import runner
from col_pipeline.errors import QualityGateError
from col_pipeline.load import PostgresLoader
from col_pipeline.orchestration.resources import PipelineResource
from col_pipeline.quality import (
    CheckResult,
    Severity,
    evaluate_freshness,
    format_failures,
    kl_today,
)
from col_pipeline.runner import LandedBatch
from col_pipeline.sources.base import Source

if TYPE_CHECKING:  # not a public Dagster name; only needed for type hints
    from dagster._core.definitions.unresolved_asset_job_definition import (
        UnresolvedAssetJobDefinition,
    )

GROUP = "ingestion"

# Two retry layers, deliberately on different time scales (see DECISIONS.md #20).
# tenacity inside the HTTP client absorbs blips of seconds; Dagster retries whole steps after
# minutes, which also lets an API rate-limit window fully reset.
LANDED_RETRY = dg.RetryPolicy(max_retries=2, delay=300, backoff=dg.Backoff.EXPONENTIAL)
RAW_RETRY = dg.RetryPolicy(max_retries=3, delay=30, backoff=dg.Backoff.EXPONENTIAL)


class IngestConfig(dg.Config):
    """Run-time options, editable in the Dagster launchpad. Defaults give a normal run."""

    full_refresh: bool = False
    since: str | None = None  # YYYY-MM-DD, for a backfill


@dataclass(frozen=True)
class SourceAssets:
    source: Source
    landed: dg.AssetsDefinition
    raw: dg.AssetsDefinition
    freshness: dg.AssetChecksDefinition
    job: "UnresolvedAssetJobDefinition"


def landed_name(source: Source) -> str:
    return f"{source.name}_landed"


def raw_name(source: Source) -> str:
    return f"{source.name}_raw"


def job_name(source: Source) -> str:
    return f"{source.name}_job"


def _as_metadata(results: list[CheckResult]) -> dict[str, str]:
    return {
        r.name: f"{'PASS' if r.passed else 'FAIL'} [{r.severity.value}] {r.detail}" for r in results
    }


def _parse_since(config: IngestConfig) -> date | None:
    if config.full_refresh and config.since:
        raise dg.Failure("Use either full_refresh or since, not both.", allow_retries=False)
    if not config.since:
        return None
    try:
        return date.fromisoformat(config.since)
    except ValueError:
        raise dg.Failure(f"since={config.since!r} is not YYYY-MM-DD", allow_retries=False) from None


def build_source_assets(
    source: Source,
    landed_retry: dg.RetryPolicy = LANDED_RETRY,
    raw_retry: dg.RetryPolicy = RAW_RETRY,
) -> SourceAssets:
    """Retry policies are parameters only so tests can use millisecond delays."""
    landed_key, raw_key = landed_name(source), raw_name(source)

    @dg.asset(
        name=landed_key,
        group_name=GROUP,
        kinds={"python"},
        retry_policy=landed_retry,
        description=f"{source.description}: fetch, land raw JSON, run the quality gate.",
        check_specs=[
            dg.AssetCheckSpec("quality_gate", asset=landed_key, blocking=True),
            dg.AssetCheckSpec("quality_warnings", asset=landed_key),
        ],
    )
    def landed(context: dg.AssetExecutionContext, config: IngestConfig, pipeline: PipelineResource):
        since = _parse_since(config)
        settings = pipeline.settings()
        engine = pipeline.engine(settings)
        try:
            batch = runner.land(
                source, settings, engine, full_refresh=config.full_refresh, since=since
            )
            gate = runner.validate(source, batch, engine)
        finally:
            engine.dispose()

        errors = [c for c in gate.checks if c.severity is Severity.ERROR]
        warnings = [c for c in gate.checks if c.severity is Severity.WARN]
        yield dg.AssetCheckResult(
            check_name="quality_gate",
            passed=gate.passed,
            severity=dg.AssetCheckSeverity.ERROR,
            description=("All blocking checks passed" if gate.passed else format_failures(errors)),
            metadata=_as_metadata(errors),
        )
        yield dg.AssetCheckResult(
            check_name="quality_warnings",
            passed=all(c.passed for c in warnings),
            severity=dg.AssetCheckSeverity.WARN,
            description=format_failures(warnings) or "No warnings",
            metadata=_as_metadata(warnings),
        )
        # If the gate failed, Dagster raises after this point (the check is blocking), so the
        # downstream _raw asset never runs. The landed file stays on disk for inspection.
        yield dg.Output(
            batch,
            metadata={
                "run_id": batch.run_id,
                "records": batch.n_records,
                "landed_file": batch.path,
                "since": batch.since or "default",
            },
        )

    @dg.asset(
        name=raw_key,
        group_name=GROUP,
        kinds={"postgres"},
        retry_policy=raw_retry,
        ins={"batch": dg.AssetIn(key=landed_key)},
        description=f"Upsert the landed batch into {source.table.qualified} (idempotent).",
    )
    def raw(context: dg.AssetExecutionContext, batch: LandedBatch, pipeline: PipelineResource):
        engine = pipeline.engine()
        try:
            summary = runner.load_batch(source, engine, batch)
        except QualityGateError as exc:
            # Deterministic: retrying cannot fix bad data, so do not retry.
            raise dg.Failure(str(exc), allow_retries=False) from exc
        finally:
            engine.dispose()
        return dg.MaterializeResult(
            metadata={
                "table": source.table.qualified,
                "rows_inserted": summary.rows_inserted,
                "rows_updated": summary.rows_updated,
                "rows_unchanged": summary.rows_unchanged,
            }
        )

    @dg.asset_check(
        asset=raw_key,
        name="freshness",
        blocking=True,
        description=(
            f"Newest {source.table.date_column} must be at most "
            f"{source.rules.max_age_days} days old."
        ),
    )
    def freshness(pipeline: PipelineResource) -> dg.AssetCheckResult:
        engine = pipeline.engine()
        try:
            latest = PostgresLoader(engine).get_watermark(source.table)
        finally:
            engine.dispose()
        result = evaluate_freshness(latest, source.rules, kl_today())
        return dg.AssetCheckResult(
            passed=result.passed,
            severity=dg.AssetCheckSeverity.ERROR,
            description=result.detail,
            metadata={"newest_date": str(latest), "max_age_days": source.rules.max_age_days},
        )

    job = dg.define_asset_job(
        job_name(source),
        selection=dg.AssetSelection.assets(landed_key, raw_key),
        description=f"Ingest {source.name}: land, gate, load, check freshness.",
    )
    return SourceAssets(source=source, landed=landed, raw=raw, freshness=freshness, job=job)


def table_row_count(pipeline: PipelineResource, source: Source) -> int:
    """Small helper for tests and notebooks."""
    engine = pipeline.engine()
    try:
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT count(*) FROM {source.table.qualified}")).scalar_one()
    finally:
        engine.dispose()
