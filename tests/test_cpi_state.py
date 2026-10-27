from datetime import date

import pytest

from col_pipeline.errors import DataContractError
from col_pipeline.extract import DataGovMyClient
from col_pipeline.quality import blocking_failures, evaluate_frame
from col_pipeline.sources import cpi_state
from col_pipeline.sources.cpi_state import OUTPUT_COLUMNS, cpi_params, to_frame


def test_maps_api_names_to_table_names(cpi_records):
    df = to_frame(cpi_records)
    assert list(df.columns) == OUTPUT_COLUMNS
    row = df[(df.state == "Johor") & (df.division == "07") & (df.month_date == date(2024, 6, 1))]
    assert row["cpi_index"].tolist() == [126.3]


def test_one_row_per_state_month_division(cpi_records):
    df = to_frame(cpi_records)
    assert len(df) == 8
    assert not df.duplicated(subset=["state", "month_date", "division"]).any()


def test_divisions_stay_text_codes_and_are_zero_padded():
    records = [
        {"state": "Johor", "date": "2024-05-01", "division": 7, "index": 125.9},
        {"state": "Johor", "date": "2024-05-01", "division": " Overall ", "index": 132.4},
    ]
    assert set(to_frame(records)["division"]) == {"07", "overall"}


def test_state_names_are_trimmed():
    records = [{"state": "  Johor ", "date": "2024-05-01", "division": "01", "index": 120.0}]
    assert to_frame(records)["state"].tolist() == ["Johor"]


def test_unparseable_index_rows_are_dropped_not_loaded():
    records = [
        {"state": "Johor", "date": "2024-05-01", "division": "01", "index": "n/a"},
        {"state": "Johor", "date": "2024-05-01", "division": "02", "index": 120.0},
    ]
    assert to_frame(records)["division"].tolist() == ["02"]


def test_index_is_rounded_to_the_column_scale():
    records = [{"state": "Johor", "date": "2024-05-01", "division": "01", "index": 120.123456}]
    assert to_frame(records)["cpi_index"].tolist() == [120.123]


def test_duplicates_collapse_keeping_the_last_value(cpi_records):
    revised = {**cpi_records[0], "index": 140.0}
    df = to_frame([*cpi_records, revised])
    row = df[
        (df.state == "Johor") & (df.division == "overall") & (df.month_date == date(2024, 5, 1))
    ]
    assert row["cpi_index"].tolist() == [140.0]


def test_a_date_that_is_not_the_first_of_the_month_stops_the_run(cpi_records):
    cpi_records[0]["date"] = "2024-05-15"
    with pytest.raises(DataContractError, match="not the 1st"):
        to_frame(cpi_records)


def test_empty_and_missing_columns_fail_loudly(cpi_records):
    with pytest.raises(DataContractError, match="zero records"):
        to_frame([])
    for r in cpi_records:
        r.pop("division")
    with pytest.raises(DataContractError, match="division"):
        to_frame(cpi_records)


def test_transform_is_deterministic(cpi_records):
    first, second = to_frame(cpi_records), to_frame(cpi_records)
    assert first.equals(second)


def test_implausible_index_blocks_the_load(cpi_records):
    cpi_records[0]["index"] = 1324.0  # a decimal point slipped
    results = evaluate_frame(to_frame(cpi_records), cpi_state.TABLE, cpi_state.RULES)
    # (min_rows also fails: the 8-row sample is far below a month's 224 rows)
    assert "range:cpi_index" in [r.name for r in blocking_failures(results)]


def test_group_size_warns_about_an_incomplete_month(cpi_records):
    results = evaluate_frame(to_frame(cpi_records), cpi_state.TABLE, cpi_state.RULES)
    warn = next(r for r in results if r.name == "group_size:month_date")
    assert not warn.passed and "224" in warn.detail


def test_params_with_and_without_a_start_date():
    assert cpi_params() == {"id": "cpi_state", "sort": "date"}
    assert cpi_params(date(2026, 7, 1))["date_start"] == "2026-07-01@date"


def test_fetch_requests_the_cpi_dataset(monkeypatch, settings):
    seen = {}

    def fake_catalogue(self, params):
        seen.update(params)
        return []

    monkeypatch.setattr(DataGovMyClient, "get_catalogue", fake_catalogue)
    cpi_state.fetch(settings, date(2026, 7, 1))
    assert seen == {"id": "cpi_state", "sort": "date", "date_start": "2026-07-01@date"}
