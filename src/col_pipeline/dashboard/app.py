"""The dashboard. Start it with `python tasks.py dashboard`.

Step 4.1 skeleton: it proves the app connects as the read-only role and can read the central
mart. The pages that answer the central question come in later steps.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from col_pipeline.dashboard.db import MART_SCHEMA, make_engine, row_count

CENTRAL_MART = "fct_fuel_vs_transport_cpi"


@st.cache_resource
def engine() -> Engine:
    return make_engine()


def first_line(exc: BaseException) -> str:
    """The driver's own message without SQLAlchemy's statement and link, which add nothing here."""
    message = str(getattr(exc, "orig", None) or exc).strip()
    return message.splitlines()[0] if message else type(exc).__name__


st.set_page_config(page_title="Malaysia cost of living", layout="wide")
st.title("Does a RON95 price change reach transport CPI?")

try:
    rows = row_count(engine(), CENTRAL_MART)
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

st.metric(f"Rows in {MART_SCHEMA}.{CENTRAL_MART}", f"{rows:,}")
