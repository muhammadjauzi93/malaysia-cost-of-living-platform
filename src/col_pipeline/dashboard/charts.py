"""Altair charts for the dashboard. Each function takes DataFrames and returns a chart spec, so the
labelling rules are tested on the spec without a browser (tests/test_dashboard_charts.py).

Rules every chart keeps (DECISIONS.md #33, #42 and #63):
- a partial fuel month is marked on the chart itself, not only in a table;
- a month awaiting CPI is labelled "awaiting CPI", never drawn as zero;
- the fuel price is labelled national wherever it appears next to a state.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from col_pipeline.dashboard.analysis import AWAITING, DIRECTIONS, FALL, NO_CHANGE, PARTIAL, RISE

# Matches the theme in .streamlit/config.toml. A fuel rise costs the public money, so it is red.
RISE_COLOR, FALL_COLOR, HELD_COLOR = "#F87171", "#34D399", "#94A3B8"
ACCENT, PARTIAL_COLOR, MUTED = "#60A5FA", "#FBBF24", "#64748B"
LABEL_COLOR = "#E2E8F0"  # value labels on the dark background
# Fuels are categories, not good or bad news, so no red or green: the headline fuel gets the accent.
SERIES_COLORS = [ACCENT, "#A78BFA", PARTIAL_COLOR, "#38BDF8", "#94A3B8", "#FB923C"]
DIRECTION_COLORS = alt.Scale(domain=DIRECTIONS, range=[FALL_COLOR, HELD_COLOR, RISE_COLOR])
DIRECTION_LABELS = {FALL: "After a fall", NO_CHANGE: "After no change", RISE: "After a rise"}
REGION_COLORS = alt.Scale(domain=["Peninsular", "East Malaysia"], range=[ACCENT, "#A78BFA"])

PARTIAL_LABEL = "Partial month"
AWAITING_LABEL = "Awaiting CPI"

# Vega-Lite cannot stretch a stacked (concatenated) chart to its container, so charts that must
# line up month for month are drawn as separate charts. They share an explicit list of months and
# reserve the same width for y-axis labels, which keeps every month in the same column.
TIMELINE_AXIS_WIDTH = 72
HEATMAP_AXIS_WIDTH = 120


def with_month(df: pd.DataFrame) -> pd.DataFrame:
    """Add `month`, a short label such as "Sep 26", used as the shared x category."""
    return df.assign(month=df["month_date"].dt.strftime("%b %y"))


def month_order(df: pd.DataFrame) -> list[str]:
    months = df.drop_duplicates("month_date").sort_values("month_date")
    return with_month(months)["month"].tolist()


def _month_x(months: list[str], labels: bool = True) -> alt.X:
    if labels:
        axis = alt.Axis(labelAngle=-45, title=None)
    else:
        axis = alt.Axis(labels=False, ticks=False, domain=False, title=None)
    return alt.X("month:O", sort=months, scale=alt.Scale(domain=months), axis=axis)


def _fixed_axis(width: int, **kwargs) -> alt.Axis:
    return alt.Axis(minExtent=width, maxExtent=width, **kwargs)


def fuel_and_transport_timeline(
    national: pd.DataFrame, state_rows: pd.DataFrame, fuel: str, state: str
) -> tuple[alt.LayerChart, alt.LayerChart]:
    """Two aligned charts. Top: the national fuel price by month. Bottom: one state's transport
    CPI change by month.

    Dashed rules mark months when the fuel average moved, so a reader can look one bar to the
    right for the effect. The partial month is shaded in both; awaiting months are labelled.
    """
    df = national[["month_date", "fuel_avg_price_myr", "fuel_mom_change_myr", "fuel_month_status"]]
    df = df.merge(
        state_rows[["month_date", "transport_cpi_mom_pct", "cpi_status"]], on="month_date"
    )
    df = with_month(df).assign(
        fuel_change_sen=df["fuel_mom_change_myr"] * 100,
        moved=df["fuel_mom_change_myr"].abs() >= 0.005,
    )
    months = month_order(df)
    partial = df[df["fuel_month_status"] == PARTIAL]
    awaiting = df[df["cpi_status"] == AWAITING]
    published = df[df["cpi_status"] != AWAITING]
    moved = df[df["moved"]]
    x = _month_x(months)

    def shading(label: str, height: int, axis_x: alt.X) -> alt.LayerChart:
        band = alt.Chart(partial).mark_rect(color=PARTIAL_COLOR, opacity=0.14).encode(x=axis_x)
        text = (
            alt.Chart(partial)
            .mark_text(color=PARTIAL_COLOR, fontSize=11, angle=270, align="right", dx=0)
            .encode(x=axis_x, y=alt.value(8), text=alt.value(label))
        )
        return band + text

    def rules(axis_x: alt.X) -> alt.Chart:
        return alt.Chart(moved).mark_rule(color=MUTED, strokeDash=[4, 3]).encode(x=axis_x)

    top_x = _month_x(months, labels=False)
    price = alt.Chart(df).encode(
        x=top_x,
        y=alt.Y(
            "fuel_avg_price_myr:Q",
            title=f"{fuel} national, RM/litre",
            scale=alt.Scale(zero=False, padding=12),
            axis=_fixed_axis(TIMELINE_AXIS_WIDTH, format=".2f"),
        ),
        tooltip=[
            alt.Tooltip("month_date:T", title="Month", format="%B %Y"),
            alt.Tooltip(
                "fuel_avg_price_myr:Q", title=f"{fuel} national average (RM)", format=".3f"
            ),
            alt.Tooltip("fuel_change_sen:Q", title="Change on month (sen)", format="+.1f"),
            alt.Tooltip("fuel_month_status:N", title="Fuel month"),
        ],
    )
    top = alt.layer(
        shading(PARTIAL_LABEL, 220, top_x),
        rules(top_x),
        price.mark_line(color=ACCENT, strokeWidth=2.5),
        price.mark_point(filled=True, size=55, color=ACCENT).encode(
            opacity=alt.condition(
                alt.datum.fuel_month_status == PARTIAL, alt.value(0.45), alt.value(1)
            )
        ),
    ).properties(height=220, width="container")

    bars = (
        alt.Chart(published)
        .mark_bar(cornerRadiusEnd=2)
        .encode(
            x=x,
            y=alt.Y(
                "transport_cpi_mom_pct:Q",
                title=f"{state} transport CPI, %",
                axis=_fixed_axis(TIMELINE_AXIS_WIDTH, format="+.1f"),
            ),
            color=alt.condition(
                alt.datum.transport_cpi_mom_pct >= 0, alt.value(RISE_COLOR), alt.value(FALL_COLOR)
            ),
            tooltip=[
                alt.Tooltip("month_date:T", title="Month", format="%B %Y"),
                alt.Tooltip(
                    "transport_cpi_mom_pct:Q", title="Transport CPI change (%)", format="+.2f"
                ),
            ],
        )
    )
    waiting = (
        alt.Chart(awaiting)
        .mark_text(color=MUTED, angle=270, fontSize=11)
        .encode(x=x, y=alt.value(80), text=alt.value(AWAITING_LABEL))
    )
    bottom = alt.layer(shading("", 190, x), rules(x), bars, waiting).properties(
        height=190, width="container"
    )
    return top, bottom


def pass_through_bars(by_direction: pd.DataFrame, state_points: pd.DataFrame | None = None):
    """Average transport CPI change in the month after a fuel fall, hold and rise.

    `state_points`, if given, marks one state's own averages as white ticks over the bars.
    """
    df = by_direction.assign(
        label=by_direction["direction"].map(DIRECTION_LABELS),
        caption=by_direction.apply(
            lambda r: f"{r.mean_transport_mom_pct:+.2f}% · {r.months} mo", axis=1
        ),
    )
    order = [DIRECTION_LABELS[d] for d in DIRECTIONS]
    x = alt.X("label:N", sort=order, title=None, axis=alt.Axis(labelAngle=0))
    y = alt.Y("mean_transport_mom_pct:Q", title="Transport CPI next month, % (avg)")
    base = alt.Chart(df).encode(x=x)
    layers = [
        base.mark_bar(cornerRadiusEnd=3, size=56).encode(
            y=y,
            color=alt.Color("direction:N", scale=DIRECTION_COLORS, legend=None),
            tooltip=[
                alt.Tooltip("label:N", title="Fuel last month"),
                alt.Tooltip(
                    "mean_transport_mom_pct:Q", title="Avg transport CPI change (%)", format="+.3f"
                ),
                alt.Tooltip("months:Q", title="Months"),
                alt.Tooltip("observations:Q", title="State-months"),
            ],
        ),
        base.mark_text(dy=-10, fontSize=12, fontWeight=600, color=LABEL_COLOR).encode(
            y=y, text="caption:N"
        ),
    ]
    if state_points is not None and not state_points.empty:
        pts = state_points.assign(label=state_points["direction"].map(DIRECTION_LABELS))
        layers.append(
            alt.Chart(pts)
            .mark_tick(color="white", thickness=3, size=44)
            .encode(
                x=x,
                y="mean_transport_mom_pct:Q",
                tooltip=[
                    alt.Tooltip(
                        "mean_transport_mom_pct:Q", title="Selected state (%)", format="+.3f"
                    )
                ],
            )
        )
    return alt.layer(*layers).properties(height=280, width="container")


def state_ranking_bars(ranked: pd.DataFrame, highlight: str | None = None) -> alt.Chart:
    """States ranked by transport CPI change in one month, coloured by region."""
    order = ranked["state_name"].tolist()
    y = alt.Y("state_name:N", sort=order, title=None, axis=alt.Axis(labelOverlap=False))
    base = alt.Chart(ranked).encode(y=y)
    bars = base.mark_bar(cornerRadiusEnd=2).encode(
        x=alt.X("transport_cpi_mom_pct:Q", title="Transport CPI, % on month"),
        color=alt.Color(
            "region:N", scale=REGION_COLORS, legend=alt.Legend(title=None, orient="bottom")
        ),
        opacity=(
            alt.condition(alt.datum.state_name == highlight, alt.value(1), alt.value(0.55))
            if highlight
            else alt.value(0.9)
        ),
        tooltip=[
            alt.Tooltip("rank:Q", title="Rank"),
            alt.Tooltip("state_name:N", title="State"),
            alt.Tooltip("transport_cpi_mom_pct:Q", title="Transport CPI change (%)", format="+.2f"),
            alt.Tooltip("transport_cpi:Q", title="Index (2010 = 100)", format=".1f"),
        ],
    )
    text = base.mark_text(align="left", dx=4, fontSize=11, color=LABEL_COLOR).encode(
        x="transport_cpi_mom_pct:Q", text=alt.Text("transport_cpi_mom_pct:Q", format="+.2f")
    )
    return (bars + text).properties(height=alt.Step(24), width="container")


def state_heatmap(fvc: pd.DataFrame, national: pd.DataFrame) -> tuple[alt.Chart, alt.LayerChart]:
    """Two aligned charts: a strip of the national fuel change by month, above every state's
    transport CPI change by month.

    Read down a column one step right of a fuel bar to see whether the states moved after it.
    Awaiting cells are grey and labelled, not coloured as zero.
    """
    months = month_order(fvc)
    strip_data = with_month(national).assign(fuel_change_sen=national["fuel_mom_change_myr"] * 100)
    strip = (
        alt.Chart(strip_data)
        .mark_bar()
        .encode(
            x=_month_x(months, labels=False),
            y=alt.Y(
                "fuel_change_sen:Q",
                title="Fuel, sen",
                axis=_fixed_axis(HEATMAP_AXIS_WIDTH, tickCount=3),
            ),
            color=alt.condition(
                alt.datum.fuel_change_sen > 0, alt.value(RISE_COLOR), alt.value(FALL_COLOR)
            ),
            tooltip=[
                alt.Tooltip("month_date:T", title="Month", format="%B %Y"),
                alt.Tooltip("fuel_change_sen:Q", title="National fuel change (sen)", format="+.1f"),
            ],
        )
        .properties(height=70, width="container")
    )
    x = _month_x(months)
    state_order = fvc.sort_values(["region", "state_name"])["state_name"].unique().tolist()
    y = alt.Y(
        "state_name:N",
        sort=state_order,
        title=None,
        axis=_fixed_axis(HEATMAP_AXIS_WIDTH, labelOverlap=False),
    )
    cells_data = with_month(fvc)
    published = cells_data[cells_data["cpi_status"] != AWAITING]
    awaiting = cells_data[cells_data["cpi_status"] == AWAITING]
    limit = float(published["transport_cpi_mom_pct"].abs().max() or 1)
    cells = (
        alt.Chart(published)
        .mark_rect(stroke="#0F172A", strokeWidth=1)
        .encode(
            x=x,
            y=y,
            color=alt.Color(
                "transport_cpi_mom_pct:Q",
                title="Transport CPI, % on month",
                scale=alt.Scale(scheme="redblue", reverse=True, domain=[-limit, limit]),
                legend=alt.Legend(orient="bottom", gradientLength=220),
            ),
            tooltip=[
                alt.Tooltip("state_name:N", title="State"),
                alt.Tooltip("month_date:T", title="Month", format="%B %Y"),
                alt.Tooltip(
                    "transport_cpi_mom_pct:Q", title="Transport CPI change (%)", format="+.2f"
                ),
            ],
        )
    )
    waiting = (
        alt.Chart(awaiting)
        .mark_rect(color="#1E293B", stroke="#0F172A")
        .encode(x=x, y=y, tooltip=[alt.Tooltip("cpi_status:N", title=AWAITING_LABEL)])
    )
    waiting_text = (
        alt.Chart(awaiting)
        .mark_text(color=MUTED, fontSize=10)
        .encode(x=x, y=y, text=alt.value("…"))
    )
    grid = alt.layer(cells, waiting, waiting_text).properties(
        height=alt.Step(22), width="container"
    )
    return strip, grid


def fuel_price_steps(
    prices: pd.DataFrame, events: pd.DataFrame, order: list[str] | None = None
) -> alt.LayerChart:
    """Weekly pump prices as steps (a price holds until the next announcement), with changes
    marked and reviewed events written on the chart. `order` puts the headline fuel first, so it
    takes the accent colour."""
    color = alt.Color(
        "fuel_name:N",
        title=None,
        scale=alt.Scale(domain=order, range=SERIES_COLORS)
        if order
        else alt.Scale(range=SERIES_COLORS),
        legend=alt.Legend(orient="bottom"),
    )
    x = alt.X("price_date:T", title=None, axis=alt.Axis(format="%b %Y"))
    base = alt.Chart(prices).encode(x=x, color=color)
    steps = base.mark_line(interpolate="step-after", strokeWidth=2.2).encode(
        y=alt.Y(
            "price_myr:Q", title="RM per litre (national)", scale=alt.Scale(zero=False, padding=10)
        )
    )
    changes = (
        alt.Chart(prices[prices["is_price_change"].fillna(False)])
        .mark_point(filled=True, size=60)
        .encode(
            x=x,
            y="price_myr:Q",
            color=color,
            tooltip=[
                alt.Tooltip("price_date:T", title="Announced", format="%d %b %Y"),
                alt.Tooltip("fuel_name:N", title="Fuel"),
                alt.Tooltip("previous_price_myr:Q", title="Was (RM)", format=".2f"),
                alt.Tooltip("price_myr:Q", title="Now (RM)", format=".2f"),
                alt.Tooltip("change_myr:Q", title="Change (RM)", format="+.2f"),
            ],
        )
    )
    layers = [steps, changes]
    if not events.empty:
        rules = (
            alt.Chart(events)
            .mark_rule(color=MUTED, strokeDash=[2, 3])
            .encode(x="price_date:T", tooltip=[alt.Tooltip("event_description:N", title="Event")])
        )
        labels = (
            alt.Chart(events)
            .mark_text(align="left", dx=4, dy=-6, angle=0, color="#CBD5E1", fontSize=10, limit=180)
            .encode(x="price_date:T", y=alt.value(12), text="event_description:N")
        )
        layers += [rules, labels]
    return alt.layer(*layers).properties(height=360, width="container")


def division_bars(yoy: pd.DataFrame) -> alt.Chart:
    """One state's year-on-year CPI change by division: transport highlighted, all items a rule."""
    items = yoy[~yoy["is_all_items"].astype(bool)]
    overall = yoy[yoy["is_all_items"].astype(bool)]
    y = alt.Y(
        "division_name:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260, labelOverlap=False)
    )
    bars = (
        alt.Chart(items)
        .mark_bar(cornerRadiusEnd=2)
        .encode(
            y=y,
            x=alt.X("yoy_pct:Q", title="% change on a year earlier"),
            color=alt.condition(
                alt.datum.division_code == "07", alt.value(ACCENT), alt.value(MUTED)
            ),
            tooltip=[
                alt.Tooltip("division_name:N", title="Division"),
                alt.Tooltip("yoy_pct:Q", title="Year on year (%)", format="+.2f"),
            ],
        )
    )
    text = (
        alt.Chart(items)
        .mark_text(align="left", dx=4, fontSize=11, color=LABEL_COLOR)
        .encode(y=y, x="yoy_pct:Q", text=alt.Text("yoy_pct:Q", format="+.1f"))
    )
    layers = [bars, text]
    if not overall.empty:
        layers.append(
            alt.Chart(overall)
            .mark_rule(color=PARTIAL_COLOR, strokeDash=[5, 3], strokeWidth=2)
            .encode(
                x="yoy_pct:Q",
                tooltip=[alt.Tooltip("yoy_pct:Q", title="All items (%)", format="+.2f")],
            )
        )
    return alt.layer(*layers).properties(height=alt.Step(26), width="container")


def yoy_lines(series: pd.DataFrame, order: list[str]) -> alt.Chart:
    """Year-on-year change over time for named series (columns: month_date, series, yoy_pct).

    The first name in `order` takes the accent colour."""
    return (
        alt.Chart(series)
        .mark_line(point=alt.OverlayMarkDef(size=28), strokeWidth=2.2)
        .encode(
            x=alt.X("month_date:T", title=None, axis=alt.Axis(format="%b %y")),
            y=alt.Y("yoy_pct:Q", title="% change on a year earlier"),
            color=alt.Color(
                "series:N",
                title=None,
                scale=alt.Scale(domain=order, range=SERIES_COLORS),
                legend=alt.Legend(orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("month_date:T", title="Month", format="%B %Y"),
                alt.Tooltip("series:N", title="Series"),
                alt.Tooltip("yoy_pct:Q", title="Year on year (%)", format="+.2f"),
            ],
        )
        .properties(height=300, width="container")
    )
