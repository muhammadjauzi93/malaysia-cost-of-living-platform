"""Fuel prices page: the weekly national pump prices, every change, and reviewed events."""

import streamlit as st

from col_pipeline.dashboard import analysis, charts, data, ui

prices = data.mart("fct_fuel_prices")
fuels = data.mart("dim_fuel_type").sort_values(
    ["is_headline", "fuel_family", "fuel_type"], ascending=[False, False, True]
)
headline = fuels.loc[fuels["is_headline"].astype(bool), "fuel_type"].iloc[0]
fuel = analysis.fuel_label(headline)

st.title("Pump prices")
st.caption(
    "Retail fuel prices are set nationally and announced every week, so every state pays the same "
    "price. A price holds until the next announcement changes it."
)

with st.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
    chosen = st.pills(
        "Fuels",
        fuels["fuel_type"].tolist(),
        selection_mode="multi",
        default=[headline],
        format_func=analysis.fuel_label,
        key="fuels",
    )
    months = ui.period_picker()
chosen = chosen or [headline]

if any(fuels.set_index("fuel_type").loc[f, "fuel_family"] == "diesel" for f in chosen):
    st.warning(
        "The diesel series have open data-quality findings: for long stretches two diesel types "
        "carry exactly the same price, which has not been reviewed yet. Treat diesel figures with "
        f"caution. {fuel}, the fuel the dashboard's answer uses, is not affected.",
        title="Diesel under review",
        icon=":material/report:",
    )

view = analysis.last_months(prices, months, "price_date")
shown = view[view["fuel_type"].isin(chosen)].assign(
    fuel_name=lambda d: d["fuel_type"].map(analysis.fuel_label)
)
changes = analysis.price_changes(view, headline)
now = analysis.price_change_since(prices, headline, days=365)

cards = st.columns(4)
with cards[0]:
    st.metric(
        f"{fuel} now",
        f"RM{now.price_now:.2f}",
        f"{now.change_sen:+.0f} sen" if now.change_sen is not None else None,
        delta_color="inverse",
        delta_description=f"since {ui.day_label(now.date_then)}",
        border=True,
        height="stretch",
    )
with cards[1]:
    st.metric(
        f"{fuel} changes",
        f"{len(changes)}",
        border=True,
        height="stretch",
        help="Announcements that changed the price, in the selected period.",
    )
rises, falls = changes[changes["change_myr"] > 0], changes[changes["change_myr"] < 0]
with cards[2]:
    st.metric(
        "Biggest rise",
        f"{rises['change_myr'].max() * 100:+.0f} sen" if len(rises) else "None",
        border=True,
        height="stretch",
        help=f"Largest single increase in {fuel}, selected period.",
    )
with cards[3]:
    st.metric(
        "Biggest fall",
        f"{falls['change_myr'].min() * 100:+.0f} sen" if len(falls) else "None",
        border=True,
        height="stretch",
        help=f"Largest single decrease in {fuel}, selected period.",
    )

with st.container(border=True):
    st.subheader("Weekly prices")
    events = shown[shown["event_description"].notna()].drop_duplicates("price_date")
    order = [analysis.fuel_label(f) for f in chosen]
    st.altair_chart(
        charts.fuel_price_steps(shown, events, order),
        alt="Weekly national pump prices for the selected fuels, with price changes marked",
    )
    st.caption(
        "Dots mark announcements that changed the price. Dotted lines are reviewed events "
        "(policy changes and similar), recorded in the project's events list."
    )

with st.container(border=True):
    st.subheader(f"Every {fuel} change")
    if changes.empty:
        st.info(f"{fuel} did not change in this period.")
    else:
        st.dataframe(
            changes.fillna({"event_description": ""}),
            hide_index=True,
            column_config={
                "price_date": st.column_config.DateColumn("Announced", format="D MMM YYYY"),
                "previous_price_myr": st.column_config.NumberColumn("Was", format="RM%.2f"),
                "price_myr": st.column_config.NumberColumn("Now", format="RM%.2f"),
                "change_myr": st.column_config.NumberColumn("Change", format="%+.2f"),
                "event_description": st.column_config.TextColumn("Reviewed event", width="large"),
            },
            alt=f"Every change in the {fuel} price, newest first",
        )
