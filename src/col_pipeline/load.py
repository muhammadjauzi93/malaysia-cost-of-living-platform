"""Load: talk to Postgres and nothing else. Works for any `TableSpec`."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from psycopg2.extras import execute_values
from sqlalchemy import Engine, text

from col_pipeline.sources.base import TableSpec

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UpsertStatements:
    create_stage: str
    insert_stage: str
    merge: str


def build_upsert_statements(table: TableSpec) -> UpsertStatements:
    """SQL for the stage-then-merge upsert, derived from the table spec.

    Identifiers are interpolated, which is safe only because `TableSpec` validates them.
    Values always travel as bind parameters.
    """
    cols = ", ".join(table.columns)
    stage = f"stg_{table.table}"
    keys = ", ".join(table.key_columns)
    assign = ", ".join(f"{c} = EXCLUDED.{c}" for c in table.value_columns)
    current = ", ".join(f"t.{c}" for c in table.value_columns)
    incoming = ", ".join(f"EXCLUDED.{c}" for c in table.value_columns)

    return UpsertStatements(
        create_stage=(
            f"CREATE TEMP TABLE {stage} (LIKE {table.qualified} INCLUDING DEFAULTS) ON COMMIT DROP"
        ),
        # `VALUES %s` is the placeholder psycopg2's execute_values expands into batched rows.
        insert_stage=f"INSERT INTO {stage} ({cols}) VALUES %s",
        # The WHERE clause skips rows whose values did not change, so loaded_at only moves
        # when data really changed. (xmax = 0) is true for a fresh insert, false for an update.
        merge=(
            f"INSERT INTO {table.qualified} AS t ({cols}) "
            f"SELECT {cols} FROM {stage} "
            f"ON CONFLICT ({keys}) DO UPDATE SET {assign}, loaded_at = now() "
            f"WHERE ({current}) IS DISTINCT FROM ({incoming}) "
            "RETURNING (xmax = 0) AS inserted"
        ),
    )


def frame_to_records(df: pd.DataFrame) -> list[dict[str, object]]:
    """DataFrame -> list of dicts with NaN/NaT turned into None, so Postgres gets NULL."""
    as_objects = df.astype(object)
    return as_objects.where(df.notna(), None).to_dict("records")


class PostgresLoader:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_watermark(self, table: TableSpec) -> date | None:
        """Latest date already loaded into `table`, or None if it is empty."""
        sql = f"SELECT max({table.date_column}) FROM {table.qualified}"
        with self.engine.connect() as conn:
            return conn.execute(text(sql)).scalar()

    def upsert(self, table: TableSpec, df: pd.DataFrame) -> dict[str, int]:
        """Idempotent load. Running it twice with the same df changes nothing the second time."""
        stmts = build_upsert_statements(table)
        records = frame_to_records(df[list(table.columns)])
        rows = [tuple(r[c] for c in table.columns) for r in records]
        with self.engine.begin() as conn:  # one transaction: all rows commit, or none do
            conn.execute(text(stmts.create_stage))
            # execute_values sends thousands of rows per statement. A plain executemany makes
            # one round trip per row, which was 4.5x slower for a 43k-row CPI history and gets
            # worse the further the database is (Docker Desktop on Windows, a cloud warehouse).
            with conn.connection.driver_connection.cursor() as cur:
                execute_values(cur, stmts.insert_stage, rows, page_size=2000)
            flags = conn.execute(text(stmts.merge)).scalars().all()

        inserted = sum(flags)
        updated = len(flags) - inserted
        log.info(
            "Upsert into %s complete: %d inserted, %d updated, %d unchanged",
            table.qualified,
            inserted,
            updated,
            len(rows) - len(flags),
        )
        return {"rows_inserted": inserted, "rows_updated": updated}


class RunLogger:
    """Writes one row per run to ops.pipeline_runs.

    A run moves running -> success or running -> failed. It may also move failed -> success:
    Dagster retries a failed load step with the same run_id, and when the retry works the log
    must say so. Nothing ever overwrites a success, so a late `fail` cannot hide loaded data.
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def start(self, pipeline_name: str, watermark_from: date | None) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                text(
                    "INSERT INTO ops.pipeline_runs (pipeline_name, watermark_from) "
                    "VALUES (:name, :wm) RETURNING run_id"
                ),
                {"name": pipeline_name, "wm": watermark_from},
            ).scalar_one()

    def succeed(
        self,
        run_id: int,
        rows_extracted: int,
        rows_inserted: int,
        rows_updated: int,
        landed_file: str,
    ) -> None:
        self._finish(
            run_id,
            ("running", "failed"),  # failed: an earlier attempt of this run, now retried
            status="success",
            error_message=None,  # clear the failed attempt's error
            rows_extracted=rows_extracted,
            rows_inserted=rows_inserted,
            rows_updated=rows_updated,
            landed_file=landed_file,
        )

    def fail(self, run_id: int, error_message: str) -> None:
        self._finish(run_id, ("running",), status="failed", error_message=error_message[:2000])

    def abandon_stale(self, pipeline_name: str, older_than: timedelta = timedelta(hours=6)) -> int:
        """Close runs of this pipeline that never finished, e.g. because the process was killed.

        Without this a crashed run stays 'running' forever and looks like it is still working.
        Only runs older than `older_than` are touched, so a genuinely concurrent run is safe.
        """
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE ops.pipeline_runs SET status = 'failed', ended_at = now(), "
                    "error_message = :msg "
                    "WHERE pipeline_name = :name AND status = 'running' "
                    "AND started_at < now() - make_interval(secs => :secs)"
                ),
                {
                    "name": pipeline_name,
                    "secs": older_than.total_seconds(),
                    "msg": f"Abandoned: still 'running' after {older_than}; process likely died",
                },
            )
        if result.rowcount:
            log.warning("[%s] closed %d abandoned run(s)", pipeline_name, result.rowcount)
        return result.rowcount

    def _finish(self, run_id: int, from_statuses: tuple[str, ...], **fields: object) -> None:
        """Close a run, but only if its current status is one of `from_statuses`."""
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    f"UPDATE ops.pipeline_runs SET ended_at = now(), {cols} "
                    "WHERE run_id = :id AND status = ANY(:from_statuses)"
                ),
                {"id": run_id, "from_statuses": list(from_statuses), **fields},
            )
