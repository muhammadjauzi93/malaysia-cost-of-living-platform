"""About the data: where the numbers come from, how current they are, and what is still open."""

import pandas as pd
import streamlit as st

from col_pipeline.dashboard import data

health = data.mart("mart_data_health")

st.title("About the data")
st.caption(
    "Three public sources, loaded by a tested pipeline into Postgres and modelled with dbt. "
    "This dashboard reads only the final `marts` tables, through a read-only database role."
)

with st.container(border=True):
    st.subheader("Sources")
    table = health.assign(
        last_load=health["last_success_at"].map(
            lambda t: "Never run by the pipeline" if pd.isna(t) else t.strftime("%d %b %Y %H:%M")
        ),
        real_rows=health["row_count"] - health["synthetic_row_count"],
        last_run_status=health["last_run_status"].fillna("–"),
    )
    st.dataframe(
        table,
        hide_index=True,
        column_order=[
            "source_label",
            "row_count",
            "real_rows",
            "synthetic_row_count",
            "earliest_data_date",
            "latest_data_date",
            "last_load",
            "last_run_status",
        ],
        column_config={
            "source_label": st.column_config.TextColumn("Source", width="medium"),
            "row_count": st.column_config.NumberColumn("Rows", format="localized"),
            "real_rows": st.column_config.NumberColumn("Real rows", format="localized"),
            "synthetic_row_count": st.column_config.NumberColumn(
                "Synthetic rows", format="localized", help="Loaded by `load-sample`. Not real data."
            ),
            "earliest_data_date": st.column_config.DateColumn("From", format="MMM YYYY"),
            "latest_data_date": st.column_config.DateColumn("To", format="D MMM YYYY"),
            "last_load": st.column_config.TextColumn("Last successful load"),
            "last_run_status": st.column_config.TextColumn("Last run"),
        },
        alt="Each source's row counts, synthetic rows, date range and last pipeline run",
    )
    built = health["built_at"].max()
    st.caption(
        f"Warehouse last built {built.strftime('%d %b %Y %H:%M %Z') if pd.notna(built) else '–'}. "
        "Fuel prices are published weekly by data.gov.my, state CPI monthly by OpenDOSM "
        "about seven weeks after the month ends, and exchange rates daily by Bank Negara Malaysia."
    )

left, right = st.columns(2, gap="medium")
with left, st.container(border=True, height="stretch"):
    st.subheader("How the numbers are made")
    st.markdown(
        """
- **Monthly fuel price**: weighted by the days each weekly price was in effect, not a plain
  average of announcements.
- **Partial month**: the current month's fuel average covers the days so far. It is shaded and
  labelled on every chart, and never compared as if complete.
- **Awaiting CPI**: a month whose CPI is not published yet is labelled as awaiting, never shown
  as zero or as a fall.
- **National fuel, state CPI**: one pump price applies everywhere, so state differences come from
  CPI alone.
- **"Average state"**: a simple mean of the 16 states. The marts hold no official national CPI.
- **Rise or fall**: a monthly average that moved by less than half a sen counts as no change.
"""
    )
with right, st.container(border=True, height="stretch"):
    st.subheader("Known open issues")
    st.markdown(
        """
- **Diesel prices under review.** On the real data, two diesel types carry exactly the same price
  over long stretches. The quality rule flags it, and it is not yet reviewed. It does not affect
  RON95, which the dashboard's answer uses.
- **The review check matches a run by its start date only**, so an extended reviewed run would
  pass unnoticed.
- **Revisions are seen only when the pipeline runs.** A price revised and revised back between two
  runs leaves no trace.
- **Schedules run only while Dagster is running** (`python tasks.py dagster`).
- **A full data-health view** (freshness verdicts, the open quality findings) is planned for a
  later step.
"""
    )

st.caption(
    "Synthetic rows come only from `python tasks.py load-sample`, which refuses to run on a "
    "database that already holds data, so real and synthetic rows are never mixed."
)
