"""Proves the load is idempotent against a real Postgres.

Uses TEST_DB_URL (not DB_URL) on purpose: these tests TRUNCATE tables, so they must never
point at your dev database by accident.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from col_pipeline.load import PostgresLoader
from col_pipeline.transform import to_long_frame

pytestmark = pytest.mark.integration

SCHEMA_SQL = (Path(__file__).parents[2] / "sql" / "schema.sql").read_text()


@pytest.fixture
def engine():
    url = os.getenv("TEST_DB_URL")
    if not url:
        pytest.skip("TEST_DB_URL not set")
    eng = create_engine(url)
    with eng.begin() as conn:
        conn.exec_driver_sql(SCHEMA_SQL)
        conn.exec_driver_sql("TRUNCATE raw.fuel_price_weekly")
    yield eng
    eng.dispose()


def _count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM raw.fuel_price_weekly")).scalar_one()


def test_running_twice_changes_nothing(engine, fuel_records):
    df = to_long_frame(fuel_records)
    loader = PostgresLoader(engine)

    first = loader.upsert_fuel_prices(df)
    count_after_first = _count(engine)
    second = loader.upsert_fuel_prices(df)

    assert first == {"rows_inserted": len(df), "rows_updated": 0}
    assert second == {"rows_inserted": 0, "rows_updated": 0}
    assert _count(engine) == count_after_first == len(df)


def test_a_revised_price_updates_exactly_one_row(engine, fuel_records):
    loader = PostgresLoader(engine)
    loader.upsert_fuel_prices(to_long_frame(fuel_records))

    fuel_records[0]["ron95"] = 2.14  # source corrects a published price
    result = loader.upsert_fuel_prices(to_long_frame(fuel_records))

    assert result == {"rows_inserted": 0, "rows_updated": 1}


def test_watermark_is_latest_date(engine, fuel_records):
    loader = PostgresLoader(engine)
    assert loader.get_watermark() is None
    loader.upsert_fuel_prices(to_long_frame(fuel_records))
    assert str(loader.get_watermark()) == "2025-10-02"
