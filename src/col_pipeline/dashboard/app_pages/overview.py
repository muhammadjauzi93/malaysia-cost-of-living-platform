"""The answer page: does a headline fuel price change show up in transport CPI a month later?"""

import streamlit as st

from col_pipeline.dashboard import analysis, charts, data, ui

fvc = data.mart("fct_fuel_vs_transport_cpi")
prices = data.mart("fct_fuel_prices")
fuel_type = analysis.headline_fuel(fvc)
fuel = analysis.fuel_label(fuel_type)

st.title(f"Does a {fuel} price change reach transport CPI?")
st.caption(
    f"The {fuel} pump price is set nationally each week. Transport CPI is published by state about "
    "seven weeks after the month ends. This page lines up each month's fuel change with the next "
    "month's transport CPI."
)

with st.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
    state = ui.state_picker(fvc["state_name"])
    months = ui.period_picker()

view = analysis.last_months(fvc, months)
national = analysis.national_monthly(view)
state_rows = view[view["state_name"] == state]
pairs = analysis.lag_pairs(view)
everyone = analysis.pass_through(pairs)
mine = analysis.pass_through(pairs[pairs["state_name"] == state])
price_now = analysis.price_change_since(prices, fuel_type, days=365)
cpi_month = analysis.latest_published_month(state_rows)
cpi_latest = (
    state_rows[state_rows["month_date"] == cpi_month].iloc[0] if cpi_month is not None else None
)
changes = analysis.price_changes(analysis.last_months(prices, months, "price_date"), fuel_type)

# ---------- headline numbers ----------
cards = st.columns(4)
with cards[0]:
    st.metric(
        f"{fuel} pump price",
        f"RM{price_now.price_now:.2f}",
        f"{price_now.change_sen:+.0f} sen" if price_now.change_sen is not None else None,
        delta_color="inverse",
        delta_description=f"since {ui.day_label(price_now.date_then)}"
        if price_now.date_then is not None
        else None,
        chart_data=price_now.last_year,
        chart_type="line",
        help=(
            "National price, announced weekly. Latest announcement "
            f"{ui.day_label(price_now.date_now)}."
        ),
        border=True,
        height="stretch",
    )
with cards[1]:
    st.metric(
        f"{state} transport CPI",
        f"{cpi_latest['transport_cpi']:.1f}" if cpi_latest is not None else "–",
        f"{cpi_latest['transport_cpi_mom_pct']:+.2f}%" if cpi_latest is not None else None,
        delta_color="inverse",
        delta_description=f"on month, {ui.month_label(cpi_month)}",
        chart_data=state_rows.loc[state_rows["transport_cpi"].notna(), "transport_cpi"]
        .tail(12)
        .tolist(),
        chart_type="area",
        help="Index, 2010 = 100. The newest month is often still awaiting publication.",
        border=True,
        height="stretch",
    )
with cards[2]:
    gap = everyone.gap_pp
    st.metric(
        "Extra transport inflation after a rise",
        f"{gap:+.2f} pp" if gap is not None else "–",
        f"{everyone.months_after(analysis.RISE)} rise months",
        delta_color="gray",
        delta_arrow="off",
        delta_description=f"of {everyone.months} compared",
        help=(
            "Average transport CPI change in the month after the fuel price rose, minus the "
            "average after months when it held. All states, this period. "
            "pp = percentage points."
        ),
        border=True,
        height="stretch",
    )
with cards[3]:
    last = changes.iloc[0] if not changes.empty else None
    st.metric(
        f"{fuel} price changes",
        f"{len(changes)}",
        f"{last['change_myr'] * 100:+.0f} sen" if last is not None else None,
        delta_color="inverse",
        delta_description=f"latest, {ui.day_label(last['price_date'])}"
        if last is not None
        else None,
        help="Weekly announcements that changed the price, in the selected period.",
        border=True,
        height="stretch",
    )

# ---------- the answer in one sentence ----------
st.info(
    analysis.finding(everyone, fuel, "the average state's"),
    title="What the data says",
    icon=":material/lightbulb:",
)
detail = [f"{everyone.months} months with published CPI and a previous-month fuel change"]
if everyone.correlation is not None:
    detail.append(f"correlation r = {everyone.correlation:+.2f}")
if everyone.pp_per_10_sen is not None:
    detail.append(f"about {everyone.pp_per_10_sen:+.2f} pp of transport CPI per 10 sen of fuel")
st.caption(" · ".join(detail) + ". An association across few months, not proof of cause.")

# ---------- the evidence ----------
left, right = st.columns([3, 2], gap="medium")
with left, st.container(border=True):
    st.subheader(f"{fuel} price and {state} transport CPI")
    price_chart, cpi_chart = charts.fuel_and_transport_timeline(national, state_rows, fuel, state)
    st.altair_chart(price_chart, alt=f"{fuel} national average price by month")
    st.altair_chart(cpi_chart, alt=f"{state}'s transport CPI change by month, aligned with it")
    st.caption(
        "Dashed lines mark months when the fuel average moved: look one bar to the right for the "
        "effect. The fuel price is national, the same in every state. Shaded: the current month, "
        "averaged over the days so far."
    )
with right, st.container(border=True):
    st.subheader("Transport CPI the month after")
    st.altair_chart(
        charts.pass_through_bars(everyone.by_direction, mine.by_direction),
        alt="Average transport CPI change in the month after a fuel fall, hold and rise",
    )
    st.caption(
        f"Bars: all states. White ticks: {state}. 'mo' is the number of months behind each bar; "
        "with only a few, one unusual month moves the bar a lot."
    )

with st.expander("How this is calculated", icon=":material/calculate:"):
    st.markdown(
        f"""
- **Monthly fuel price**: the {fuel} price weighted by the days each weekly price was in effect,
  so a price that held for 25 days counts more than one that held for 5.
- **The lag**: last month's change in that average beside this month's change in transport CPI.
- **Rise, fall or no change**: a monthly average that moved by less than half a sen counts
  as no change.
- **Left out**: months still awaiting CPI (never treated as zero), and the first month, which has no
  previous change. The current month is partial and labelled.
- **"The average state"**: a simple average of the 16 states, not the official national CPI.
- Diesel is not on this page. Its series has open data-quality findings that do not affect {fuel}.
"""
    )
