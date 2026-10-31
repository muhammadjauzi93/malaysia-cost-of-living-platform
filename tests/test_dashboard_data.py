"""Postgres types become plain pandas types before any page sees them."""

import datetime as dt
from decimal import Decimal

import pandas as pd

from col_pipeline.dashboard.data import tidy


def test_numerics_become_floats_and_dates_become_datetimes():
    raw = pd.DataFrame(
        {
            "price": [Decimal("2.05"), None],
            "day": [dt.date(2026, 10, 1), None],
            "stamp": [pd.Timestamp("2026-10-31T05:00:00Z"), None],
            "name": ["ron95", "ron97"],
            "empty": [None, None],
        }
    )
    out = tidy(raw)
    assert out["price"].dtype == float and out["price"].iloc[0] == 2.05
    assert pd.api.types.is_datetime64_any_dtype(out["day"])
    assert out["day"].iloc[0] == pd.Timestamp("2026-10-01")
    assert pd.api.types.is_datetime64_any_dtype(out["stamp"])
    assert out["name"].tolist() == ["ron95", "ron97"]
    assert out["empty"].isna().all()


def test_tidy_does_not_change_its_input():
    raw = pd.DataFrame({"price": [Decimal("1.90")]})
    tidy(raw)
    assert isinstance(raw["price"].iloc[0], Decimal)
