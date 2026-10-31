"""Cost of living page: transport beside the other parts of a state's consumer price index."""

import streamlit as st

from col_pipeline.dashboard import analysis, charts, data, ui

cpi = data.mart("fct_cpi_state")
divisions = data.mart("dim_division")
states = data.mart("dim_state")

st.title("Where transport sits in the cost of living")
parts = int((~divisions["is_all_items"].astype(bool)).sum())
st.caption(
    f"Transport is one of {parts} parts of the consumer price index. Compare how fast each part is "
    "rising in a state, a year on year."
)

state = ui.state_picker(states["state_name"])
code = states.loc[states["state_name"] == state, "state_code"].iloc[0]
yoy = analysis.division_yoy(cpi, divisions, code)

if yoy.empty:
    st.info(f"No year-on-year CPI for {state} yet: it needs at least 13 months of data.")
    st.stop()

month = yoy["month_date"].iloc[0]
by_code = yoy.set_index("division_code")


def yoy_of(division: str) -> float | None:
    return float(by_code.loc[division, "yoy_pct"]) if division in by_code.index else None


cards = st.columns(4)
for card, (label, division) in zip(
    cards[:3],
    [("All items", "overall"), ("Transport", "07"), ("Food and drink", "01")],
    strict=True,
):
    value = yoy_of(division)
    with card:
        st.metric(
            label,
            f"{value:+.1f}%" if value is not None else "–",
            help=f"Change in {state}'s index on a year earlier, {ui.month_label(month)}.",
            border=True,
            height="stretch",
        )
fastest = yoy[~yoy["is_all_items"].astype(bool)].iloc[0]
with cards[3]:
    st.metric(
        "Rising fastest",
        fastest["division_name"].split(",")[0],
        f"{fastest['yoy_pct']:+.1f}%",
        delta_color="inverse",
        border=True,
        height="stretch",
    )

left, right = st.columns([1, 1], gap="medium")
with left, st.container(border=True):
    st.subheader(f"{state}, {ui.month_label(month)}")
    st.altair_chart(
        charts.division_bars(yoy),
        alt=f"Year-on-year CPI change by division in {state}, transport highlighted",
    )
    st.caption("Blue: transport. Dashed line: all items, the headline inflation rate.")
with right, st.container(border=True):
    st.subheader("Over time")
    picked = cpi[
        (cpi["state_code"] == code)
        & cpi["division_code"].isin(["07", "overall", "01"])
        & cpi["yoy_pct"].notna()
    ].merge(divisions, on="division_code")
    names = {"07": "Transport", "overall": "All items", "01": "Food and drink"}
    series = picked.assign(series=picked["division_code"].map(names))
    st.altair_chart(
        charts.yoy_lines(series, ["Transport", "All items", "Food and drink"]),
        alt=f"Year-on-year change in {state}'s transport, food and all-items CPI over time",
    )
    st.caption(
        "Above the all-items line, transport is pushing the cost of living up; "
        "below it, holding it down."
    )
