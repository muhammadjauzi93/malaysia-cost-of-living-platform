"""The synthetic sample must be deterministic, realistic, and pass the real quality gate."""

from datetime import date

import pytest

from col_pipeline.quality import blocking_failures, evaluate_frame
from col_pipeline.sample_data import (
    BUILDERS,
    DIVISIONS,
    STATES,
    build_cpi_state,
    build_fuel_prices,
    build_fx_rates,
)
from col_pipeline.sources import SOURCES

END = date(2026, 10, 27)  # a Tuesday


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_the_sample_is_deterministic(name):
    assert BUILDERS[name](END).equals(BUILDERS[name](END))


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_the_sample_passes_the_real_quality_gate(name):
    source = SOURCES[name]
    frame = BUILDERS[name](END)
    assert list(frame.columns) == list(source.table.columns)
    assert blocking_failures(evaluate_frame(frame, source.table, source.rules)) == []


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_the_newest_row_is_inside_the_freshness_limit(name):
    # CI runs `dbt source freshness` on this data, so it must never be born stale.
    source = SOURCES[name]
    newest = BUILDERS[name](END)[source.table.date_column].max()
    assert (END - newest).days <= source.rules.max_age_days


def test_cpi_has_every_state_and_division_in_every_month():
    cpi = build_cpi_state(END)
    assert cpi["state"].nunique() == len(STATES) == 16
    assert set(cpi["division"]) == set(DIVISIONS)
    assert all(d.day == 1 for d in cpi["month_date"])
    per_month = cpi.groupby("month_date").size()
    assert (per_month == 16 * 14).all()


def test_fuel_is_weekly_on_thursdays_and_a_new_fuel_type_appears_late():
    fuel = build_fuel_prices(END)
    assert all(d.weekday() == 3 for d in fuel["price_date"])
    assert {"ron95", "ron97", "diesel", "ron95_budi95"} <= set(fuel["fuel_type"])
    counts = fuel.groupby("fuel_type").size()
    assert counts["ron95_budi95"] < counts["ron95"]  # added later, like a new API column


def test_ron95_rarely_changes_which_is_why_loaded_at_cannot_measure_freshness():
    ron95 = build_fuel_prices(END).query("fuel_type == 'ron95'")
    assert ron95["price_myr"].nunique() <= len(ron95) // 4


def test_jpy_is_quoted_per_100_and_the_others_per_1():
    fx = build_fx_rates(END)
    units = fx.groupby("currency")["unit"].first().to_dict()
    assert units == {"CNY": 1, "EUR": 1, "JPY": 100, "SGD": 1, "USD": 1}
    assert (fx["buying_rate"] <= fx["selling_rate"]).all()
    assert all(d.weekday() < 5 for d in fx["rate_date"])
