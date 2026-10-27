import copy
from datetime import date

import pandas as pd
import pytest

from col_pipeline.errors import DataContractError
from col_pipeline.extract import DataGovMyClient
from col_pipeline.quality import blocking_failures, evaluate_frame
from col_pipeline.sources import fuel_prices
from col_pipeline.sources.fuel_prices import OUTPUT_COLUMNS, fuel_price_params, to_frame


def test_output_has_exact_columns_in_order(fuel_records):
    df = to_frame(fuel_records)
    assert list(df.columns) == OUTPUT_COLUMNS


def test_change_weekly_rows_are_excluded(fuel_records):
    # The change row holds negative deltas; if it leaked through, the range check would fail
    # and 2017-04-06 ron95 would have two candidate values.
    df = to_frame(fuel_records)
    row = df[(df.price_date == pd.Timestamp("2017-04-06").date()) & (df.fuel_type == "ron95")]
    assert row["price_myr"].tolist() == [2.16]


def test_null_prices_are_dropped_not_loaded(fuel_records):
    df = to_frame(fuel_records)
    early = df[df.price_date < pd.Timestamp("2025-01-01").date()]
    assert "ron95_budi95" not in set(early.fuel_type)  # did not exist in 2017/2024


def test_new_fuel_columns_flow_through_without_code_changes(fuel_records):
    # Schema evolution: BUDI95 and SKPS columns appeared in 2025. Long format absorbs them.
    df = to_frame(fuel_records)
    assert {"ron95_budi95", "ron95_skps"} <= set(df.fuel_type)


def test_one_row_per_primary_key(fuel_records):
    duplicated = fuel_records + [copy.deepcopy(fuel_records[0])]
    df = to_frame(duplicated)
    assert not df.duplicated(subset=["price_date", "fuel_type"]).any()
    assert len(df) == len(to_frame(fuel_records))


def test_transform_is_deterministic(fuel_records):
    pd.testing.assert_frame_equal(to_frame(fuel_records), to_frame(fuel_records))


def test_empty_extract_fails_loudly():
    with pytest.raises(DataContractError, match="zero records"):
        to_frame([])


def test_missing_required_column_fails(fuel_records):
    for r in fuel_records:
        r.pop("series_type")
    with pytest.raises(DataContractError, match="series_type"):
        to_frame(fuel_records)


@pytest.mark.parametrize("bad_price", [0.0, 45.0])
def test_implausible_price_fails_the_quality_gate(fuel_records, bad_price):
    # Phase 1 raised inside transform. The range check now lives in the quality rules (so it
    # carries a severity), but an implausible price still blocks the load.
    fuel_records[0]["ron95"] = bad_price
    df = to_frame(fuel_records)
    failures = blocking_failures(evaluate_frame(df, fuel_prices.TABLE, fuel_prices.RULES))
    assert [f.name for f in failures] == ["range:price_myr"]


def test_clean_fixture_passes_every_check(fuel_records):
    df = to_frame(fuel_records)
    results = evaluate_frame(df, fuel_prices.TABLE, fuel_prices.RULES)
    assert all(r.passed for r in results)


def test_full_history_params():
    assert fuel_price_params() == {"id": "fuelprice", "filter": "level@series_type", "sort": "date"}


def test_incremental_params_add_date_start():
    assert fuel_price_params(date(2026, 9, 1))["date_start"] == "2026-09-01@date"


def test_fetch_passes_the_watermark_through(monkeypatch, settings):
    seen = {}

    def fake_catalogue(self, params):
        seen.update(params)
        return [{"date": "2026-10-22", "series_type": "level", "ron95": 2.05}]

    monkeypatch.setattr(DataGovMyClient, "get_catalogue", fake_catalogue)
    fuel_prices.fetch(settings, date(2026, 10, 1))
    assert seen["date_start"] == "2026-10-01@date"
