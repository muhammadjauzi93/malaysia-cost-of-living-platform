"""The dashboard's read layer can only address tables in `marts` (DECISIONS.md #58).

The database role is the second guard; tests/integration/test_dashboard_role.py proves that one.
"""

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from col_pipeline.dashboard.db import NotAMartError, make_engine, mart_table


def sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def test_a_mart_name_becomes_a_table_in_marts():
    statement = select(func.count()).select_from(mart_table("fct_fuel_vs_transport_cpi"))
    assert "FROM marts.fct_fuel_vs_transport_cpi" in sql(statement)


@pytest.mark.parametrize(
    "name",
    [
        "raw.fuel_price_weekly",  # another schema
        "ops.pipeline_runs",
        "marts.fct_fuel_prices",  # even marts itself: the schema is never the caller's choice
        "fct_fuel_prices; DROP TABLE marts.dim_state",  # SQL in a name
        'fct_fuel_prices"',
        "fct fuel prices",
        "FCT_FUEL_PRICES",  # dbt names are lower case; a quoted upper-case name is another table
        "",
        "1table",
        None,
    ],
)
def test_anything_but_a_bare_lower_case_name_is_refused(name):
    with pytest.raises(NotAMartError, match="marts"):
        mart_table(name)


def test_engine_connects_as_the_reader_never_the_owner():
    # Read-only sessions are observed against a real database in the integration test.
    env = {
        "POSTGRES_USER": "col_user",
        "POSTGRES_PASSWORD": "owner-secret",
        "POSTGRES_DB": "col_platform",
        "DASHBOARD_DB_PASSWORD": "reader-secret",
    }
    engine = make_engine(env)
    try:
        url = make_url(engine.url)
        assert (url.username, url.password, url.database) == (
            "dashboard_reader",
            "reader-secret",
            "col_platform",
        )
    finally:
        engine.dispose()
