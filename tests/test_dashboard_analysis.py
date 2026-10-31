"""Every number the dashboard shows, checked against values worked out by hand.

The fixture (tests/dashboard_fixtures.py) lays the arithmetic out in its docstring.
"""

import numpy as np
import pandas as pd
import pytest

from col_pipeline.dashboard import analysis as an
from tests import dashboard_fixtures as fx


@pytest.fixture
def fvc() -> pd.DataFrame:
    return fx.fuel_vs_cpi()


# ---------- small rules ----------


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (0.05, an.RISE),
        (0.005, an.RISE),
        (0.0049, an.NO_CHANGE),  # day weighting leaves dust in the fourth decimal
        (0.0, an.NO_CHANGE),
        (-0.0049, an.NO_CHANGE),
        (-0.10, an.FALL),
        (np.nan, None),
        (None, None),
    ],
)
def test_direction_ignores_moves_under_half_a_sen(change, expected):
    assert an.direction(change) == expected


def test_fuel_labels_read_naturally_including_unknown_types():
    assert an.fuel_label("ron95") == "RON95"
    assert an.fuel_label("diesel_eastmsia") == "Diesel (East Malaysia)"
    assert an.fuel_label("ron100_new") == "RON100 NEW"


def test_headline_fuel_is_read_from_the_mart(fvc):
    assert an.headline_fuel(fvc) == "ron95"
    assert an.headline_fuel(fvc.assign(fuel_type="ron97")) == "ron97"


def test_headline_fuel_refuses_a_mart_with_two_fuels(fvc):
    mixed = fvc.copy()
    mixed.loc[0, "fuel_type"] = "diesel"
    with pytest.raises(ValueError, match="one headline fuel"):
        an.headline_fuel(mixed)


def test_last_months_counts_back_from_the_newest_month(fvc):
    assert an.last_months(fvc, 3)["month_date"].dt.month.unique().tolist() == [4, 5, 6]
    assert len(an.last_months(fvc, None)) == len(fvc)


# ---------- the answer ----------


def test_change_pairs_drop_awaiting_months_and_the_first_month(fvc):
    pairs = an.change_pairs(fvc)
    assert sorted(pairs["month_date"].dt.month.unique()) == [2, 3, 4, 5]
    assert (pairs["cpi_status"] == an.PUBLISHED).all()
    march = pairs[pairs["month_date"] == "2026-03-01"]
    assert set(march["fuel_direction"]) == {an.RISE}


def test_pass_through_matches_the_hand_calculation(fvc):
    result = an.pass_through(an.change_pairs(fvc))
    assert result.mean_when(an.RISE) == pytest.approx(0.4)
    assert result.mean_when(an.NO_CHANGE) == pytest.approx(0.2)
    assert result.gap_pp == pytest.approx(0.2)
    assert result.months_when(an.RISE) == 1
    assert result.months_when(an.NO_CHANGE) == 2
    assert result.months == 4
    # The May fall is felt in May, whose CPI is published.
    assert result.months_when(an.FALL) == 1
    assert result.mean_when(an.FALL) == pytest.approx(-0.1 / 3)


def test_pass_through_reports_what_last_months_change_adds(fvc):
    result = an.pass_through(an.change_pairs(fvc))
    assert result.next_month_pp_per_10_sen == pytest.approx(-1 / 9)
    # Without months that have both changes there is nothing to separate.
    alone = an.pass_through(an.change_pairs(fvc.assign(fuel_mom_change_prev_month_myr=np.nan)))
    assert alone.next_month_pp_per_10_sen is None


def test_pass_through_for_one_state(fvc):
    pairs = an.change_pairs(fvc)
    selangor = an.pass_through(pairs[pairs["state_name"] == "Selangor"])
    assert selangor.mean_when(an.RISE) == pytest.approx(0.6)
    assert selangor.mean_when(an.NO_CHANGE) == pytest.approx(0.15)
    assert selangor.gap_pp == pytest.approx(0.45)


def test_correlation_needs_variation_in_both_series(fvc):
    pairs = an.change_pairs(fvc)
    assert an.pass_through(pairs).correlation is not None
    flat = pairs.assign(fuel_mom_change_myr=0.0)
    assert an.pass_through(flat).correlation is None
    assert an.pass_through(flat).pp_per_10_sen is None


def test_finding_states_the_numbers_and_never_more(fvc):
    sentence = an.finding(an.pass_through(an.change_pairs(fvc)), "RON95", "the average state's")
    assert sentence == (
        "In the 1 month when RON95 rose, the average state's transport CPI changed by +0.40% "
        "on average in the same month, against +0.20% in months when the price held."
    )


def test_finding_says_when_there_is_nothing_to_measure(fvc):
    no_rise = fvc.assign(fuel_mom_change_myr=0.0)
    assert "did not rise" in an.finding(an.pass_through(an.change_pairs(no_rise)), "RON95", "x")
    nothing = fvc.assign(cpi_status=an.AWAITING)
    assert "no month" in an.finding(an.pass_through(an.change_pairs(nothing)), "RON95", "x")


def test_national_monthly_averages_states_but_not_awaiting_months(fvc):
    national = an.national_monthly(fvc).set_index("month_date")
    assert national.loc["2026-05-01", "transport_cpi_mom_pct"] == pytest.approx(-0.1 / 3)
    assert national.loc["2026-05-01", "states_published"] == 3
    june = national.loc["2026-06-01"]
    assert np.isnan(june["transport_cpi_mom_pct"]) and june["states_published"] == 0
    assert june["fuel_month_status"] == an.PARTIAL
    # Fuel is national: one value per month, whatever the number of states.
    assert national.loc["2026-03-01", "fuel_avg_price_myr"] == pytest.approx(2.10)


# ---------- states ----------


def test_ranking_uses_the_latest_published_month_never_an_awaiting_one(fvc):
    month, ranked = an.state_ranking(fvc)
    assert month == pd.Timestamp("2026-05-01")
    assert ranked[["rank", "state_name"]].values.tolist() == [
        [1, "Sabah"],
        [2, "Selangor"],
        [3, "Johor"],
    ]


def test_ranking_is_empty_when_nothing_is_published(fvc):
    month, ranked = an.state_ranking(fvc.assign(cpi_status=an.AWAITING))
    assert month is None and ranked.empty


def test_sensitivity_by_state(fvc):
    table = an.state_sensitivity(an.change_pairs(fvc)).set_index("state_name")
    assert table.loc["Selangor", "gap_pp"] == pytest.approx(0.45)
    assert table.loc["Johor", "gap_pp"] == pytest.approx(0.2)
    assert table.loc["Sabah", "gap_pp"] == pytest.approx(-0.05)
    assert table.index.tolist() == ["Selangor", "Johor", "Sabah"]
    assert (table["rises"] == 1).all()


def test_sensitivity_of_nothing_is_an_empty_table():
    empty = an.state_sensitivity(an.change_pairs(fx.fuel_vs_cpi().iloc[0:0]))
    assert empty.empty and "gap_pp" in empty.columns


# ---------- fuel and cost of living ----------


def test_price_change_since_a_year_ago():
    now = an.price_change_since(fx.fuel_prices(), "ron95", days=365)
    assert now.price_now == pytest.approx(2.00)
    assert now.date_now == pd.Timestamp("2026-06-04")
    # 4 Jun 2025 is before the first price (1 Jun 2025 holds), so that is the comparison.
    assert now.date_then == pd.Timestamp("2025-06-01")
    assert now.change_sen == pytest.approx(-5.0)
    assert now.last_year == [2.05, 2.05, 2.10, 2.00, 2.00]


def test_price_change_since_falls_back_to_the_first_price_on_a_short_series():
    short = fx.fuel_prices()
    short = short[(short["fuel_type"] == "ron95") & (short["price_date"] >= "2026-03-01")]
    now = an.price_change_since(short, "ron95", days=365)
    assert now.date_then == pd.Timestamp("2026-03-05")
    assert now.change_sen == pytest.approx(-10.0)


def test_price_changes_lists_only_real_changes_newest_first():
    changes = an.price_changes(fx.fuel_prices(), "ron95")
    assert changes["price_date"].dt.strftime("%Y-%m-%d").tolist() == ["2026-05-07", "2026-03-05"]
    assert changes["change_myr"].tolist() == pytest.approx([-0.10, 0.05])
    assert changes["event_description"].iloc[1] == "Subsidy rationalisation, phase one"


def test_division_yoy_uses_the_latest_month_with_a_figure():
    yoy = an.division_yoy(fx.cpi_state(), fx.divisions(), "MY-10")
    assert (yoy["month_date"] == pd.Timestamp("2026-05-01")).all()
    assert yoy["division_name"].tolist() == [
        "Transport",
        "All items",
        "Food and non-alcoholic beverages",
        "Housing",
    ]
    assert an.division_yoy(fx.cpi_state(), fx.divisions(), "MY-99").empty


def test_synthetic_sources_lists_only_sources_with_synthetic_rows():
    assert an.synthetic_sources(fx.data_health(synthetic=True)) == [
        "State CPI (OpenDOSM)",
        "Fuel prices (data.gov.my)",
    ]
    assert an.synthetic_sources(fx.data_health(synthetic=False)) == []
