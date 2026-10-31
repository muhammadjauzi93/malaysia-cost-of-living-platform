"""States page: which states' transport CPI moves most, and which react most to a fuel rise."""

import streamlit as st

from col_pipeline.dashboard import analysis, charts, data, ui

fvc = data.mart("fct_fuel_vs_transport_cpi")
fuel = analysis.fuel_label(analysis.headline_fuel(fvc))

st.title("Which states feel it most?")
st.caption(
    f"The {fuel} price is the same everywhere; what differs is how each state's transport costs "
    "respond. Compare states in the latest published month, over time, and when fuel rises."
)

months = ui.period_picker()
view = analysis.last_months(fvc, months)
national = analysis.national_monthly(view)
month, ranked = analysis.state_ranking(view)
sensitivity = analysis.state_sensitivity(analysis.change_pairs(view))
awaiting = view.loc[view["cpi_status"] == analysis.AWAITING, "month_date"].drop_duplicates()

# ---------- headline numbers ----------
cards = st.columns(4)
if not ranked.empty:
    top, bottom = ranked.iloc[0], ranked.iloc[-1]
    with cards[0]:
        st.metric(
            "Highest transport CPI change",
            top["state_name"],
            f"{top['transport_cpi_mom_pct']:+.2f}%",
            delta_color="inverse",
            delta_description=ui.month_label(month),
            border=True,
            height="stretch",
        )
    with cards[1]:
        st.metric(
            "Lowest",
            bottom["state_name"],
            f"{bottom['transport_cpi_mom_pct']:+.2f}%",
            delta_color="inverse",
            delta_description=ui.month_label(month),
            border=True,
            height="stretch",
        )
    with cards[2]:
        spread = top["transport_cpi_mom_pct"] - bottom["transport_cpi_mom_pct"]
        st.metric(
            "Spread between states",
            f"{spread:.2f} pp",
            border=True,
            height="stretch",
            help="Highest minus lowest transport CPI change in the month. pp = percentage points.",
        )
reactive = sensitivity.dropna(subset=["gap_pp"])
with cards[3]:
    if reactive.empty:
        st.metric(
            "Most reactive to a rise",
            "–",
            border=True,
            height="stretch",
            help="Needs at least one fuel rise and one unchanged month in the period.",
        )
    else:
        lead = reactive.iloc[0]
        st.metric(
            "Most reactive to a rise",
            lead["state_name"],
            f"{lead['gap_pp']:+.2f} pp",
            delta_color="inverse",
            delta_description="extra in a rise month",
            border=True,
            height="stretch",
            help="Transport CPI change in fuel-rise months minus no-change months, in that state.",
        )
if len(awaiting):
    st.caption(
        f"{', '.join(ui.month_label(m) for m in awaiting)}: transport CPI not yet published. "
        "Shown as awaiting and never ranked, because a missing figure is not a fall."
    )

# ---------- the comparison ----------
left, right = st.columns([2, 3], gap="medium")
with left, st.container(border=True):
    st.subheader(f"Ranked, {ui.month_label(month)}")
    if ranked.empty:
        st.info("No month in this period has published transport CPI yet.")
    else:
        st.altair_chart(
            charts.state_ranking_bars(ranked, st.session_state.get("state")),
            alt=f"States ranked by transport CPI change in {ui.month_label(month)}",
        )
        st.caption("Bright bar: the state picked on the other pages.")
with right, st.container(border=True):
    st.subheader("Month by month")
    strip, grid = charts.state_heatmap(view, national)
    st.altair_chart(strip, alt="National fuel price change by month")
    st.altair_chart(grid, alt="Transport CPI change for every state and month")
    st.caption(
        f"Top strip: the national {fuel} change each month. Red cells: transport CPI rose. "
        "Read straight down from a fuel bar for the same month. "
        "Dark cells with '…' are awaiting CPI."
    )

with st.container(border=True):
    st.subheader("When fuel rises, by state")
    st.dataframe(
        sensitivity,
        hide_index=True,
        height="content",
        column_order=["state_name", "region", "with_rise", "with_no_change", "gap_pp", "rises"],
        column_config={
            "state_name": st.column_config.TextColumn("State"),
            "region": st.column_config.TextColumn("Region"),
            "with_rise": st.column_config.NumberColumn("Rise months", format="%+.2f%%"),
            "with_no_change": st.column_config.NumberColumn("No-change months", format="%+.2f%%"),
            "gap_pp": st.column_config.NumberColumn(
                "Extra in a rise month",
                format="%+.2f pp",
                help="Rise months minus no-change months.",
            ),
            "rises": st.column_config.NumberColumn(
                "Number of rises", help="Months behind 'Rise months'."
            ),
        },
        alt="Each state's average transport CPI change in fuel-rise and no-change months",
    )
    st.caption(
        "Sorted by the extra change in a rise month. With few rise months, treat the order as a "
        "pointer to look closer, not a league table."
    )
