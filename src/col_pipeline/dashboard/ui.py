"""Small pieces of page UI shared by every page: the synthetic-data banner and the pickers.

The state and period pickers keep their value for the whole session, so a choice made on one
page carries over to the next.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from col_pipeline.dashboard.analysis import synthetic_sources

DEFAULT_STATE = "Selangor"
PERIODS = {"12 months": 12, "24 months": 24, "All": None}


def synthetic_banner(health: pd.DataFrame) -> None:
    """Say plainly when any data on screen is synthetic. Rows come from `load-sample`."""
    sources = synthetic_sources(health)
    if sources:
        st.warning(
            "This database holds generated demonstration data, loaded with `load-sample`, in "
            f"place of: {'; '.join(sources)}. The numbers are not real prices or CPI, and no "
            "finding on these pages describes Malaysia.",
            title="Synthetic data",
            icon=":material/science:",
        )


def state_picker(states: pd.Series) -> str:
    options = sorted(states.dropna().unique())
    if "state" not in st.session_state:
        st.session_state["state"] = DEFAULT_STATE if DEFAULT_STATE in options else options[0]
    return st.selectbox("State", options, key="state", persist_state="session", width=240)


def period_picker() -> int | None:
    """Number of months to show, or None for the whole history."""
    choice = st.segmented_control(
        "Period",
        list(PERIODS),
        default="24 months",
        required=True,
        key="period",
        persist_state="session",
    )
    return PERIODS[choice or "24 months"]


def month_label(month: pd.Timestamp | None) -> str:
    return "–" if month is None or pd.isna(month) else month.strftime("%B %Y")


def day_label(day: pd.Timestamp | None) -> str:
    return "–" if day is None or pd.isna(day) else f"{day.day} {day.strftime('%b %Y')}"
