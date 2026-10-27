"""Shared fixtures for tests that need a real Postgres.

They read TEST_DB_URL, never DB_URL, because they TRUNCATE tables. When it is not set, the
tests skip instead of failing, so `pytest` works on a laptop with no database running.
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

SCHEMA_SQL = (Path(__file__).parents[2] / "sql" / "schema.sql").read_text()
TABLES = [
    "raw.fuel_price_weekly",
    "raw.cpi_state_monthly",
    "raw.fx_rate_daily",
    "ops.pipeline_runs",
]


@pytest.fixture
def test_db_url() -> str:
    url = os.getenv("TEST_DB_URL")
    if not url:
        pytest.skip("TEST_DB_URL not set")
    return url


@pytest.fixture
def engine(test_db_url):
    eng = create_engine(test_db_url)
    with eng.begin() as conn:
        conn.exec_driver_sql(SCHEMA_SQL)
        conn.exec_driver_sql(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY")
    yield eng
    eng.dispose()


@pytest.fixture
def count(engine):
    def _count(qualified_table: str, where: str = "true") -> int:
        with engine.connect() as conn:
            return conn.execute(
                text(f"SELECT count(*) FROM {qualified_table} WHERE {where}")
            ).scalar_one()

    return _count


@pytest.fixture
def runs(engine):
    """All rows of ops.pipeline_runs, oldest first, as dicts."""

    def _runs() -> list[dict]:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM ops.pipeline_runs ORDER BY run_id")).mappings()
            return [dict(r) for r in rows]

    return _runs
