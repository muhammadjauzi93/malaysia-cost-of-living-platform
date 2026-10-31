"""Cached mart reads for the pages. Every page gets its data from `mart()`, and nothing else.

Each mart is read whole and filtered in pandas: the largest holds a few thousand rows, so there
is no SQL filtering to write, and so no user input ever reaches a query (DECISIONS.md #63).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import Engine

from col_pipeline.dashboard.db import make_engine, read_mart

# Marts change only when the pipeline or dbt runs, so a short cache costs nothing in freshness
# and saves a query on every click.
CACHE_TTL = "10m"


@st.cache_resource
def engine() -> Engine:
    return make_engine()


@st.cache_data(ttl=CACHE_TTL, show_spinner="Reading the warehouse…")
def mart(name: str) -> pd.DataFrame:
    """Every row of `marts.<name>`, with database types turned into plain pandas types."""
    return tidy(read_mart(engine(), name))


def tidy(df: pd.DataFrame) -> pd.DataFrame:
    """Postgres numerics arrive as Decimal and dates as datetime.date, both in object columns.

    Charts and arithmetic need floats and datetimes. Columns that are entirely null are left alone.
    """
    out = df.copy()
    for column in out.columns:
        if out[column].dtype != object:
            continue
        sample = out[column].dropna()
        if sample.empty:
            continue
        first = sample.iloc[0]
        if isinstance(first, Decimal):
            out[column] = pd.to_numeric(out[column], errors="coerce").astype(float)
        elif isinstance(first, dt.date) and not isinstance(first, dt.datetime):
            out[column] = pd.to_datetime(out[column])
    return out
