"""Entry point: orchestrates extract -> land -> transform -> load, and records the run.

    python -m col_pipeline.main                 # incremental (default)
    python -m col_pipeline.main --full-refresh  # ignore the watermark, pull all history

In Phase 2, Dagster replaces this file. Everything it calls stays the same.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine

from col_pipeline.config import Settings
from col_pipeline.extract import (
    FUELPRICE_DATASET_ID,
    DataGovMyClient,
    fetch_fuel_prices,
    land_raw,
)
from col_pipeline.load import PostgresLoader, RunLogger
from col_pipeline.logging_config import setup_logging
from col_pipeline.transform import to_long_frame

PIPELINE_NAME = "fuel_price_weekly"
log = logging.getLogger("col_pipeline.main")


def resolve_since(watermark: date | None, lookback_days: int, full_refresh: bool) -> date | None:
    """Where to start extracting from.

    We re-fetch a few weeks *behind* the watermark so that revised prices are picked up.
    The upsert makes the overlap harmless.
    """
    if full_refresh or watermark is None:
        return None
    return watermark - timedelta(days=lookback_days)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load weekly Malaysian fuel prices into Postgres.")
    parser.add_argument(
        "--full-refresh",
        action="store_true",
        help="Ignore the watermark and re-pull the full history.",
    )
    return parser.parse_args(argv)


def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = Settings.from_env()  # loads .env first, so LOG_LEVEL below can come from it
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))

    engine = create_engine(settings.db_url, pool_pre_ping=True)
    loader = PostgresLoader(engine)
    runs = RunLogger(engine, PIPELINE_NAME)

    since = resolve_since(loader.get_watermark(), settings.lookback_days, args.full_refresh)
    run_id = runs.start(watermark_from=since)
    log.info("Run %s started (since=%s)", run_id, since or "full history")

    try:
        client = DataGovMyClient(
            base_url=settings.api_base_url,
            timeout_s=settings.request_timeout_s,
            token=settings.api_token,
        )
        records = fetch_fuel_prices(client, since=since)
        landed = land_raw(records, settings.landing_dir, FUELPRICE_DATASET_ID, run_id)
        df = to_long_frame(records)
        result = loader.upsert_fuel_prices(df)
        runs.succeed(run_id, rows_extracted=len(records), landed_file=str(landed), **result)
    except Exception as exc:
        log.exception("Run %s failed", run_id)
        runs.fail(run_id, f"{type(exc).__name__}: {exc}")
        return 1
    finally:
        engine.dispose()

    log.info("Run %s succeeded", run_id)
    return 0


if __name__ == "__main__":
    sys.exit(run())
