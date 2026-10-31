"""The dashboard starts, and explains a missing setting or database instead of crashing.

Runs the real Streamlit script with Streamlit's own test harness. No database is needed, so this
also proves on every CI path, the plain-pip one included, that Streamlit installs and imports.
"""

from importlib.util import find_spec

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

# Located, not imported: importing app.py would run the whole Streamlit script.
APP_SCRIPT = find_spec("col_pipeline.dashboard.app").origin


@pytest.fixture(autouse=True)
def fresh_engine_cache(monkeypatch):
    """Each test builds its own engine. Settings come from the test, never from a local .env:
    an empty value set here is not overwritten by load_dotenv."""
    for name in ("DASHBOARD_DB_URL", "DASHBOARD_DB_PASSWORD"):
        monkeypatch.setenv(name, "")
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def run_app() -> AppTest:
    app = AppTest.from_file(APP_SCRIPT, default_timeout=30)
    return app.run()


def test_missing_reader_password_is_explained():
    app = run_app()
    assert not app.exception
    assert "DASHBOARD_DB_PASSWORD" in app.error[0].value
    assert not app.metric


def test_unreachable_database_is_explained_with_the_next_steps(monkeypatch):
    # Port 1 refuses connections at once on every OS, so this needs no Postgres.
    monkeypatch.setenv("DASHBOARD_DB_URL", "postgresql+psycopg2://r:r@127.0.0.1:1/none")
    app = run_app()
    assert not app.exception
    message = app.error[0].value
    assert "marts.fct_fuel_vs_transport_cpi" in message
    assert "create-dashboard-role" in message
    assert ":r@" not in message  # the URL, and so the password, is never shown
    assert not app.metric
