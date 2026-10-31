"""The labelling rules, checked on the chart specs (DECISIONS.md #33, #42, #63).

A partial month is marked on the chart, an awaiting month is labelled and never drawn as a bar,
the fuel price is labelled national, and charts drawn one above the other share their months.
"""

import json

import pandas as pd
import pytest

from col_pipeline.dashboard import analysis as an
from col_pipeline.dashboard import charts
from tests import dashboard_fixtures as fx


def spec(chart) -> dict:
    return chart.to_dict()


def text(chart) -> str:
    return json.dumps(spec(chart), ensure_ascii=False)


def datasets(chart) -> list[list[dict]]:
    return list(spec(chart).get("datasets", {}).values())


def x_domain(chart) -> list[str]:
    """The explicit month list of the first layer that encodes x."""
    for layer in spec(chart)["layer"]:
        for sub in layer.get("layer", [layer]):
            x = sub.get("encoding", {}).get("x")
            if x and "scale" in x:
                return x["scale"]["domain"]
    raise AssertionError("no x scale domain found")


@pytest.fixture
def fvc() -> pd.DataFrame:
    return fx.fuel_vs_cpi()


@pytest.fixture
def timeline(fvc):
    national = an.national_monthly(fvc)
    return charts.fuel_and_transport_timeline(
        national, fvc[fvc["state_name"] == "Selangor"], "RON95", "Selangor"
    )


def test_timeline_marks_the_partial_month_on_the_chart(timeline):
    top, bottom = timeline
    assert charts.PARTIAL_LABEL in text(top)
    partial_rows = [r for d in datasets(top) for r in d if r.get("fuel_month_status") == "partial"]
    assert {r["month"] for r in partial_rows} == {"Jun 26"}


def test_timeline_labels_awaiting_cpi_and_draws_no_bar_for_it(timeline):
    _, bottom = timeline
    assert charts.AWAITING_LABEL in text(bottom)
    bars = next(
        layer for layer in spec(bottom)["layer"] if layer.get("mark", {}).get("type") == "bar"
    )
    bar_rows = spec(bottom)["datasets"][bars["data"]["name"]]
    assert all(r["cpi_status"] != an.AWAITING for r in bar_rows)
    assert "Jun 26" not in {r["month"] for r in bar_rows}


def test_timeline_labels_the_fuel_price_as_national(timeline):
    top, _ = timeline
    assert "RON95 national" in text(top)


def test_timeline_panels_share_every_month(timeline):
    top, bottom = timeline
    expected = ["Jan 26", "Feb 26", "Mar 26", "Apr 26", "May 26", "Jun 26"]
    assert x_domain(top) == expected
    assert x_domain(bottom) == expected


def test_timeline_marks_the_months_the_fuel_average_moved(timeline):
    top, _ = timeline
    rules = next(
        layer for layer in spec(top)["layer"] if layer.get("mark", {}).get("type") == "rule"
    )
    moved = {r["month"] for r in spec(top)["datasets"][rules["data"]["name"]]}
    assert moved == {"Mar 26", "May 26"}


def test_heatmap_keeps_awaiting_cells_out_of_the_colour_scale(fvc):
    strip, grid = charts.state_heatmap(fvc, an.national_monthly(fvc))
    assert x_domain(grid) == x_domain(_as_layer(strip))
    cells = spec(grid)["layer"][0]
    coloured = spec(grid)["datasets"][cells["data"]["name"]]
    assert all(r["cpi_status"] == an.PUBLISHED for r in coloured)


def _as_layer(chart):
    """The strip is a plain chart; wrap it so x_domain can read it like a layer."""
    import altair as alt

    return alt.layer(chart)


def test_pass_through_bars_show_the_number_of_months_behind_each_bar(fvc):
    result = an.pass_through(an.lag_pairs(fvc))
    chart_text = text(charts.pass_through_bars(result.by_direction))
    assert "+0.40% · 1 mo" in chart_text
    assert "+0.08% · 2 mo" in chart_text


def test_ranking_chart_lists_every_state_it_is_given(fvc):
    _, ranked = an.state_ranking(fvc)
    rows = datasets(charts.state_ranking_bars(ranked, "Selangor"))[0]
    assert [r["state_name"] for r in rows] == ["Sabah", "Selangor", "Johor"]


def test_fuel_chart_draws_steps_and_writes_reviewed_events():
    prices = fx.fuel_prices().assign(fuel_name=lambda d: d["fuel_type"].map(an.fuel_label))
    events = prices[prices["event_description"].notna()]
    chart = charts.fuel_price_steps(prices, events, ["RON95", "RON97", "Diesel"])
    assert '"interpolate": "step-after"' in text(chart)
    assert "Subsidy rationalisation, phase one" in text(chart)
    assert "national" in text(chart)


def test_fuel_chart_without_events_has_no_event_layer():
    prices = fx.fuel_prices().assign(fuel_name=lambda d: d["fuel_type"].map(an.fuel_label))
    chart = charts.fuel_price_steps(prices, prices.iloc[0:0])
    assert len(spec(chart)["layer"]) == 2


def test_division_chart_highlights_transport_and_draws_all_items_as_a_line():
    yoy = an.division_yoy(fx.cpi_state(), fx.divisions(), "MY-10")
    chart_text = text(charts.division_bars(yoy))
    assert '"07"' in chart_text  # the transport highlight condition
    assert '"rule"' in chart_text  # all items
