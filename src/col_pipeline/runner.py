"""Run one source end to end, as three separable phases.

    land      call the API, write the raw payload to disk, open a run row
    validate  transform the landed payload and judge it with the quality rules (the gate)
    load      upsert into Postgres and close the run row

The CLI chains the three. Dagster runs them as separate assets, with the gate as a blocking
asset check in between. Both call exactly the same functions, so behaviour cannot diverge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import Engine

from col_pipeline.config import Settings
from col_pipeline.errors import DataContractError, QualityGateError
from col_pipeline.extract import land_raw, read_landed
from col_pipeline.load import PostgresLoader, RunLogger
from col_pipeline.quality import (
    CheckResult,
    Severity,
    blocking_failures,
    evaluate_frame,
    format_failures,
)
from col_pipeline.sources.base import Source

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LandedBatch:
    """Handle on a landed payload. Plain strings and ints, so Dagster can pickle it."""

    source: str
    run_id: int
    path: str
    n_records: int
    since: str | None


@dataclass(frozen=True)
class GateResult:
    frame: pd.DataFrame | None  # None when the structural contract itself failed
    checks: list[CheckResult]

    @property
    def passed(self) -> bool:
        return self.frame is not None and not blocking_failures(self.checks)


@dataclass(frozen=True)
class LoadSummary:
    rows_inserted: int
    rows_updated: int
    rows_in_frame: int

    @property
    def rows_unchanged(self) -> int:
        return self.rows_in_frame - self.rows_inserted - self.rows_updated


def resolve_since(
    source: Source,
    watermark: date | None,
    *,
    full_refresh: bool = False,
    explicit_since: date | None = None,
) -> date | None:
    """Where to start extracting from; None means 'whatever the source treats as the default'.

    Incremental sources re-fetch `lookback_days` *behind* the watermark so revised values are
    picked up. The upsert makes the overlap harmless. An explicit --since always wins.
    """
    if explicit_since is not None:
        return explicit_since
    if full_refresh or watermark is None or not source.incremental_from_watermark:
        return None
    return watermark - timedelta(days=source.lookback_days)


def land(
    source: Source,
    settings: Settings,
    engine: Engine,
    *,
    full_refresh: bool = False,
    since: date | None = None,
) -> LandedBatch:
    loader, runs = PostgresLoader(engine), RunLogger(engine)
    runs.abandon_stale(source.name)
    effective = resolve_since(
        source,
        loader.get_watermark(source.table),
        full_refresh=full_refresh,
        explicit_since=since,
    )
    run_id = runs.start(source.name, effective)
    log.info("[%s] run %s started (since=%s)", source.name, run_id, effective or "default")
    try:
        records = source.fetch(settings, effective)
        path = land_raw(records, settings.landing_dir, source.dataset_id, run_id)
    except Exception as exc:
        runs.fail(run_id, f"{type(exc).__name__}: {exc}")
        raise
    return LandedBatch(
        source=source.name,
        run_id=run_id,
        path=str(path),
        n_records=len(records),
        since=effective.isoformat() if effective else None,
    )


def validate(source: Source, batch: LandedBatch, engine: Engine) -> GateResult:
    """The quality gate. Marks the run failed if any ERROR-level check does not pass."""
    records = read_landed(batch.path)
    try:
        frame = source.to_frame(records)
    except DataContractError as exc:
        checks = [CheckResult("data_contract", False, Severity.ERROR, str(exc))]
        RunLogger(engine).fail(batch.run_id, f"Data contract: {exc}")
        log.error("[%s] data contract failed: %s", source.name, exc)
        return GateResult(frame=None, checks=checks)

    checks = evaluate_frame(frame, source.table, source.rules)
    for check in checks:
        level = (
            logging.INFO
            if check.passed
            else (logging.ERROR if check.severity is Severity.ERROR else logging.WARNING)
        )
        log.log(level, "[%s] check %s: %s", source.name, check.name, check.detail)

    if failures := blocking_failures(checks):
        RunLogger(engine).fail(batch.run_id, f"Quality gate: {format_failures(failures)}")
    return GateResult(frame=frame, checks=checks)


def load_batch(source: Source, engine: Engine, batch: LandedBatch) -> LoadSummary:
    """Validate again, then load. Refuses to load anything that fails the gate.

    Re-checking here means the load is safe even when someone calls it without the gate
    having run first, so the guarantee does not depend on how the steps were wired.
    """
    gate = validate(source, batch, engine)
    if not gate.passed:
        raise QualityGateError(f"[{source.name}] blocked: {format_failures(gate.checks)}")
    assert gate.frame is not None  # for type checkers: passed implies a frame

    runs = RunLogger(engine)
    try:
        result = PostgresLoader(engine).upsert(source.table, gate.frame)
    except Exception as exc:
        runs.fail(batch.run_id, f"{type(exc).__name__}: {exc}")
        raise
    runs.succeed(
        batch.run_id,
        rows_extracted=batch.n_records,
        landed_file=batch.path,
        **result,
    )
    log.info("[%s] run %s succeeded", source.name, batch.run_id)
    return LoadSummary(
        rows_inserted=result["rows_inserted"],
        rows_updated=result["rows_updated"],
        rows_in_frame=len(gate.frame),
    )


def run_source(
    source: Source,
    settings: Settings,
    engine: Engine,
    *,
    full_refresh: bool = False,
    since: date | None = None,
) -> LoadSummary:
    """land -> validate -> load, the way the CLI runs it."""
    batch = land(source, settings, engine, full_refresh=full_refresh, since=since)
    return load_batch(source, engine, batch)
