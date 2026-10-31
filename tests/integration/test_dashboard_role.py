"""The dashboard's role can read `marts` and nothing else, and keeps that after dbt rebuilds.

This is the database half of the marts-only rule (DECISIONS.md #58). The other half, the read
layer, is tests/test_dashboard_db.py.

It needs a built warehouse: in CI it runs in the dbt job after `dbt build`, and REQUIRE_DBT_TESTS
there turns a missing mart into a failure instead of a skip. It uses its own role name, never
`dashboard_reader`: roles belong to the whole Postgres server, so resetting the real role's
password here would also change it for every other database on that server.
"""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from col_pipeline.dashboard.db import read_mart, row_count
from col_pipeline.dashboard.roles import grant_dashboard_reader

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
TEST_ROLE = "dashboard_reader_test"
CENTRAL_MART = "fct_fuel_vs_transport_cpi"


@pytest.fixture
def owner(test_db_url):
    eng = create_engine(test_db_url)
    with eng.connect() as conn:
        built = conn.execute(text("SELECT to_regclass(:t)"), {"t": f"marts.{CENTRAL_MART}"})
        if built.scalar_one() is None:
            message = f"marts.{CENTRAL_MART} not built: run load-sample and dbt-build first"
            if os.getenv("REQUIRE_DBT_TESTS"):
                pytest.fail(message)
            pytest.skip(message)
    yield eng
    eng.dispose()


@pytest.fixture
def reader(owner, test_db_url):
    """An engine logged in as the test role, granted exactly as create-dashboard-role does."""
    password = secrets.token_urlsafe(16)
    with owner.begin() as conn:
        grant_dashboard_reader(conn, TEST_ROLE, password)
    url = make_url(test_db_url).set(username=TEST_ROLE, password=password)
    eng = create_engine(url)
    yield eng
    eng.dispose()


def denied(engine, statement: str) -> str:
    """Run a statement that must fail, and return Postgres' reason."""
    with pytest.raises(DBAPIError) as caught, engine.begin() as conn:
        conn.execute(text(statement))
    return str(caught.value.orig)


def test_reader_can_read_the_central_mart(reader, owner):
    with owner.connect() as conn:
        expected = conn.execute(text(f"SELECT count(*) FROM marts.{CENTRAL_MART}")).scalar_one()
    assert expected > 0
    assert row_count(reader, CENTRAL_MART) == expected
    assert len(read_mart(reader, CENTRAL_MART)) == expected


@pytest.mark.parametrize("table", ["raw.fuel_price_weekly", "ops.pipeline_runs"])
def test_reader_cannot_read_outside_marts(reader, table):
    assert "permission denied" in denied(reader, f"SELECT 1 FROM {table} LIMIT 1")


def test_reader_cannot_read_the_warehouse_layers_below_marts(reader, owner):
    with owner.connect() as conn:
        views = conn.execute(
            text(
                "SELECT table_schema || '.' || table_name FROM information_schema.tables "
                "WHERE table_schema IN ('staging', 'intermediate') ORDER BY 1"
            )
        ).scalars()
        views = list(views)
    assert views, "dbt build should have created staging and intermediate views"
    for view in views:
        assert "permission denied" in denied(reader, f"SELECT 1 FROM {view} LIMIT 1"), view


@pytest.mark.parametrize(
    "statement",
    [
        f"DELETE FROM marts.{CENTRAL_MART}",
        f"UPDATE marts.{CENTRAL_MART} SET transport_cpi = 0",
        "CREATE TABLE marts.dashboard_was_here (x int)",
    ],
)
def test_reader_cannot_write_even_in_marts(reader, statement):
    reason = denied(reader, statement)
    assert "read-only transaction" in reason or "permission denied" in reason


def test_reader_sessions_are_read_only(reader):
    with reader.connect() as conn:
        assert conn.execute(text("SHOW transaction_read_only")).scalar_one() == "on"


def test_grant_survives_a_dbt_rebuild_of_the_mart(reader, test_db_url):
    # dbt replaces the table with a new one; only the default privilege keeps the grant alive.
    rebuilt = subprocess.run(
        [sys.executable, str(ROOT / "tasks.py"), "dbt", "run", "-s", CENTRAL_MART, "--quiet"],
        cwd=ROOT,
        env={**os.environ, "DB_URL": test_db_url},
        capture_output=True,
        text=True,
    )
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert row_count(reader, CENTRAL_MART) > 0


def test_granting_twice_is_safe_and_resets_the_password(owner, reader, test_db_url):
    new_password = secrets.token_urlsafe(16)
    with owner.begin() as conn:
        grant_dashboard_reader(conn, TEST_ROLE, new_password)
    url = make_url(test_db_url).set(username=TEST_ROLE, password=new_password)
    eng = create_engine(url)
    try:
        assert row_count(eng, CENTRAL_MART) > 0
    finally:
        eng.dispose()
