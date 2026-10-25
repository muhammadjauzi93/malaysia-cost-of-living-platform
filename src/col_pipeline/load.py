"""Load: talk to Postgres and nothing else."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from sqlalchemy import Engine, text

log = logging.getLogger(__name__)

CREATE_STAGE = text(
    """
    CREATE TEMP TABLE stg_fuel_price
        (LIKE raw.fuel_price_weekly INCLUDING DEFAULTS)
        ON COMMIT DROP
    """
)

INSERT_STAGE = text(
    """
    INSERT INTO stg_fuel_price (price_date, fuel_type, price_myr)
    VALUES (:price_date, :fuel_type, :price_myr)
    """
)

# Stage-then-merge. The WHERE clause means unchanged rows are not touched, so loaded_at
# only moves when a price really changed. RETURNING (xmax = 0) is a Postgres idiom:
# true for a freshly inserted row, false for an updated one.
MERGE_FROM_STAGE = text(
    """
    INSERT INTO raw.fuel_price_weekly AS t (price_date, fuel_type, price_myr)
    SELECT price_date, fuel_type, price_myr FROM stg_fuel_price
    ON CONFLICT (price_date, fuel_type) DO UPDATE
        SET price_myr = EXCLUDED.price_myr,
            loaded_at = now()
        WHERE t.price_myr IS DISTINCT FROM EXCLUDED.price_myr
    RETURNING (xmax = 0) AS inserted
    """
)


class PostgresLoader:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get_watermark(self) -> date | None:
        """Latest price_date already loaded, or None if the table is empty."""
        with self.engine.connect() as conn:
            return conn.execute(text("SELECT max(price_date) FROM raw.fuel_price_weekly")).scalar()

    def upsert_fuel_prices(self, df: pd.DataFrame) -> dict[str, int]:
        """Idempotent load. Running it twice with the same df changes nothing the second time."""
        rows = df.to_dict("records")
        with self.engine.begin() as conn:  # one transaction: all rows commit, or none do
            conn.execute(CREATE_STAGE)
            conn.execute(INSERT_STAGE, rows)
            flags = conn.execute(MERGE_FROM_STAGE).scalars().all()

        inserted = sum(flags)
        updated = len(flags) - inserted
        log.info(
            "Upsert complete: %d inserted, %d updated, %d unchanged",
            inserted,
            updated,
            len(rows) - len(flags),
        )
        return {"rows_inserted": inserted, "rows_updated": updated}


class RunLogger:
    """Writes one row per run to ops.pipeline_runs: your first metadata table."""

    def __init__(self, engine: Engine, pipeline_name: str) -> None:
        self.engine = engine
        self.pipeline_name = pipeline_name

    def start(self, watermark_from: date | None) -> int:
        with self.engine.begin() as conn:
            return conn.execute(
                text(
                    "INSERT INTO ops.pipeline_runs (pipeline_name, watermark_from) "
                    "VALUES (:name, :wm) RETURNING run_id"
                ),
                {"name": self.pipeline_name, "wm": watermark_from},
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
            status="success",
            rows_extracted=rows_extracted,
            rows_inserted=rows_inserted,
            rows_updated=rows_updated,
            landed_file=landed_file,
        )

    def fail(self, run_id: int, error_message: str) -> None:
        self._finish(run_id, status="failed", error_message=error_message[:2000])

    def _finish(self, run_id: int, **fields: object) -> None:
        cols = ", ".join(f"{k} = :{k}" for k in fields)
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE ops.pipeline_runs SET ended_at = now(), {cols} WHERE run_id = :id"),
                {"id": run_id, **fields},
            )
