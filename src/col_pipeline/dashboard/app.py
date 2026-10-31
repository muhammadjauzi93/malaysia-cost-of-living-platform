"""The dashboard's entry point. Start it with `python tasks.py dashboard`.

Checks the database once, shows the synthetic-data banner on every page, then runs the page the
viewer picked. The pages are in app_pages/; the numbers they show come from analysis.py.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from col_pipeline.dashboard import data, ui
from col_pipeline.dashboard.db import MART_SCHEMA

CENTRAL_MART = "fct_fuel_vs_transport_cpi"


def first_line(exc: BaseException) -> str:
    """The driver's own message without SQLAlchemy's statement and link, which add nothing here."""
    message = str(getattr(exc, "orig", None) or exc).strip()
    return message.splitlines()[0] if message else type(exc).__name__


st.set_page_config(
    page_title="Fuel prices and transport CPI · Malaysia",
    page_icon=":material/local_gas_station:",
    layout="wide",
)

try:
    central = data.mart(CENTRAL_MART)
    health = data.mart("mart_data_health")
except RuntimeError as exc:  # settings missing from .env
    st.error(str(exc))
    st.stop()
except SQLAlchemyError as exc:
    st.error(
        f"Could not read {MART_SCHEMA}.{CENTRAL_MART}: {first_line(exc)}\n\n"
        "Is Postgres running (`python tasks.py up`), has the warehouse been built "
        "(`python tasks.py dbt-build`), and has the read-only role been created "
        "(`python tasks.py create-dashboard-role`)?"
    )
    st.stop()

if central.empty:
    st.info(
        "The warehouse is built but holds no data. Load it with `python tasks.py run` (real data) "
        "or, on an empty scratch database, `python tasks.py load-sample`, then "
        "`python tasks.py dbt-build`.",
        title="No data yet",
        icon=":material/database:",
    )
    st.stop()

page = st.navigation(
    [
        st.Page(
            "app_pages/overview.py", title="The answer", icon=":material/insights:", default=True
        ),
        st.Page("app_pages/states.py", title="States", icon=":material/map:"),
        st.Page("app_pages/fuel.py", title="Fuel prices", icon=":material/local_gas_station:"),
        st.Page(
            "app_pages/cost_of_living.py", title="Cost of living", icon=":material/shopping_cart:"
        ),
        st.Page("app_pages/about.py", title="About the data", icon=":material/fact_check:"),
    ],
    position="top",
)
ui.synthetic_banner(health)
page.run()
