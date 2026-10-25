import copy

import pandas as pd
import pytest

from col_pipeline.transform import OUTPUT_COLUMNS, DataContractError, to_long_frame


def test_output_has_exact_columns_in_order(fuel_records):
    df = to_long_frame(fuel_records)
    assert list(df.columns) == OUTPUT_COLUMNS


def test_change_weekly_rows_are_excluded(fuel_records):
    # The change row holds negative deltas; if it leaked through, the range check would fail
    # and 2017-04-06 ron95 would have two candidate values.
    df = to_long_frame(fuel_records)
    row = df[(df.price_date == pd.Timestamp("2017-04-06").date()) & (df.fuel_type == "ron95")]
    assert row["price_myr"].tolist() == [2.16]


def test_null_prices_are_dropped_not_loaded(fuel_records):
    df = to_long_frame(fuel_records)
    early = df[df.price_date < pd.Timestamp("2025-01-01").date()]
    assert "ron95_budi95" not in set(early.fuel_type)  # did not exist in 2017/2024


def test_new_fuel_columns_flow_through_without_code_changes(fuel_records):
    # Schema evolution: BUDI95 and SKPS columns appeared in 2025. Long format absorbs them.
    df = to_long_frame(fuel_records)
    assert {"ron95_budi95", "ron95_skps"} <= set(df.fuel_type)


def test_one_row_per_primary_key(fuel_records):
    duplicated = fuel_records + [copy.deepcopy(fuel_records[0])]
    df = to_long_frame(duplicated)
    assert not df.duplicated(subset=["price_date", "fuel_type"]).any()
    assert len(df) == len(to_long_frame(fuel_records))


def test_transform_is_deterministic(fuel_records):
    pd.testing.assert_frame_equal(to_long_frame(fuel_records), to_long_frame(fuel_records))


def test_empty_extract_fails_loudly():
    with pytest.raises(DataContractError, match="zero records"):
        to_long_frame([])


def test_missing_required_column_fails(fuel_records):
    for r in fuel_records:
        r.pop("series_type")
    with pytest.raises(DataContractError, match="series_type"):
        to_long_frame(fuel_records)


@pytest.mark.parametrize("bad_price", [0.0, 45.0])
def test_implausible_price_fails_the_contract(fuel_records, bad_price):
    fuel_records[0]["ron95"] = bad_price
    with pytest.raises(DataContractError, match="outside"):
        to_long_frame(fuel_records)
