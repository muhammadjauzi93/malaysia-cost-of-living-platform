"""The dashboard, run page by page with Streamlit's own test harness. No database is needed.

The marts are replaced by the hand-checked tables in tests/dashboard_fixtures.py, so these tests
also prove, on every CI path including plain pip, that Streamlit and Altair install and work.
The same pages are run against a real built warehouse in tests/integration/test_dashboard_role.py.
"""

import json
from importlib.util import find_spec

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from col_pipeline.dashboard import data
from tests import dashboard_fixtures as fx

# Located, not imported: importing app.py would run the whole Streamlit script.
APP_SCRIPT = find_spec("col_pipeline.dashboard.app").origin
PAGES = ["overview", "states", "fuel", "cost_of_living", "about", "glossary"]


@pytest.fixture(autouse=True)
def fresh_caches(monkeypatch):
    """Settings come from each test, never from a local .env: an empty value set here is not
    overwritten by load_dotenv. Caches are cleared so no test sees another's data."""
    for name in ("DASHBOARD_DB_URL", "DASHBOARD_DB_PASSWORD"):
        monkeypatch.setenv(name, "")
    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def use_marts(monkeypatch, tables: dict) -> None:
    monkeypatch.setattr(data, "mart", lambda name: tables[name].copy())


def run_app() -> AppTest:
    return AppTest.from_file(APP_SCRIPT, default_timeout=30).run()


def open_page(app: AppTest, page: str) -> AppTest:
    return app.switch_page(f"app_pages/{page}.py").run()


def chart_specs(app: AppTest) -> str:
    return " ".join(
        json.dumps(json.loads(el.proto.spec), ensure_ascii=False)
        for el in app.get("vega_lite_chart")
    )


# ---------- before any data ----------


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


def test_an_empty_warehouse_says_how_to_load_it(monkeypatch):
    tables = fx.marts()
    tables["fct_fuel_vs_transport_cpi"] = tables["fct_fuel_vs_transport_cpi"].iloc[0:0]
    use_marts(monkeypatch, tables)
    app = run_app()
    assert not app.exception
    assert "load-sample" in app.info[0].value


# ---------- every page ----------


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_and_labels_synthetic_data(monkeypatch, page):
    use_marts(monkeypatch, fx.marts(synthetic=True))
    app = open_page(run_app(), page)
    assert not app.exception, [e.value for e in app.exception]
    assert not app.error
    assert app.title
    assert any(w.proto.title == "Synthetic data" for w in app.warning)


@pytest.mark.parametrize("page", PAGES)
def test_real_data_carries_no_synthetic_banner(monkeypatch, page):
    use_marts(monkeypatch, fx.marts(synthetic=False))
    app = open_page(run_app(), page)
    assert not app.exception
    assert not any(w.proto.title == "Synthetic data" for w in app.warning)


# ---------- the answer page ----------


def test_answer_page_title_comes_from_the_mart_not_the_code(monkeypatch):
    tables = fx.marts()
    tables["fct_fuel_vs_transport_cpi"] = tables["fct_fuel_vs_transport_cpi"].assign(
        fuel_type="ron97"
    )
    use_marts(monkeypatch, tables)
    app = run_app()
    assert app.title[0].value == "Does a RON97 price change reach transport CPI?"


def test_answer_page_states_the_hand_checked_finding(monkeypatch):
    use_marts(monkeypatch, fx.marts())
    app = run_app()
    assert not app.exception
    finding = next(i.value for i in app.info if "rose" in i.value)
    assert "+0.40% on average in the same month, against +0.20%" in finding
    metrics = {m.label: m.value for m in app.metric}
    assert metrics["RON95 pump price"] == "RM2.00"
    assert metrics["Extra transport inflation in a rise month"] == "+0.20 pp"
    assert metrics["RON95 price changes"] == "2"


def test_answer_page_charts_label_partial_and_awaiting_months_and_national_fuel(monkeypatch):
    use_marts(monkeypatch, fx.marts())
    specs = chart_specs(run_app())
    assert "Partial month" in specs
    assert "Awaiting CPI" in specs
    assert "RON95 national" in specs


def test_state_choice_follows_the_viewer_to_other_pages(monkeypatch):
    use_marts(monkeypatch, fx.marts())
    app = run_app()
    app.selectbox(key="state").select("Johor").run()
    assert app.metric[1].label == "Johor transport CPI"
    app = open_page(app, "cost_of_living")
    assert app.selectbox(key="state").value == "Johor"


# ---------- states and fuel pages ----------


def test_states_page_ranks_the_latest_published_month_and_says_june_is_awaiting(monkeypatch):
    use_marts(monkeypatch, fx.marts())
    app = open_page(run_app(), "states")
    assert any(s.value == "Ranked, May 2026" for s in app.subheader)
    assert any("June 2026" in c.value and "never ranked" in c.value for c in app.caption)
    assert app.metric[0].value == "Sabah"


def test_choosing_diesel_shows_the_open_data_quality_warning(monkeypatch):
    use_marts(monkeypatch, fx.marts())
    app = open_page(run_app(), "fuel")
    assert not any(w.proto.title == "Diesel under review" for w in app.warning)
    app.pills(key="fuels").set_value(["ron95", "diesel"]).run()
    assert not app.exception
    assert any(w.proto.title == "Diesel under review" for w in app.warning)


# ---------- glossary page ----------


def test_glossary_lists_acronyms_and_every_pages_numbers(monkeypatch):
    use_marts(monkeypatch, fx.marts())
    app = open_page(run_app(), "glossary")
    assert [h.value for h in app.header] == [
        "Acronyms, sources and units",
        "The numbers, page by page",
    ]
    assert [s.value for s in app.subheader] == [
        "The answer",
        "States",
        "Fuel prices",
        "Cost of living",
        "About the data",
    ]


def test_glossary_search_narrows_the_entries_and_says_when_nothing_matches(monkeypatch):
    use_marts(monkeypatch, fx.marts())
    app = open_page(run_app(), "glossary")
    app.text_input(key="glossary_query").input("percentage points").run()
    assert any(m.value.startswith("**pp**") for m in app.markdown)
    assert not any(m.value.startswith("**CPI**") for m in app.markdown)
    app.text_input(key="glossary_query").input("zzz").run()
    assert "No glossary entry" in app.info[0].value
