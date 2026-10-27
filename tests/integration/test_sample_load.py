"""Loading the synthetic sample: labelled, and impossible to mix into a database with data."""

from datetime import date

import pytest

from col_pipeline.sample_data import SAMPLE_SOURCE_LABEL, SampleDataError, load_sample

END = date(2026, 10, 27)

pytestmark = pytest.mark.integration


def test_sample_rows_are_labelled_synthetic(engine, count):
    loaded = load_sample(engine, END)
    assert loaded["cpi_state"] == 16 * 14 * 36
    for table in ("raw.fuel_price_weekly", "raw.cpi_state_monthly", "raw.fx_rate_daily"):
        assert count(table) > 0
        assert count(table, f"source <> '{SAMPLE_SOURCE_LABEL}'") == 0


def test_a_second_load_is_refused_and_changes_nothing(engine, count):
    load_sample(engine, END)
    before = count("raw.fx_rate_daily")
    with pytest.raises(SampleDataError, match="already contain data"):
        load_sample(engine, END)
    assert count("raw.fx_rate_daily") == before


def test_a_database_with_real_looking_data_is_refused(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "INSERT INTO raw.fuel_price_weekly (price_date, fuel_type, price_myr) "
            "VALUES ('2026-10-22', 'ron95', 2.05)"
        )
    with pytest.raises(SampleDataError, match="raw.fuel_price_weekly"):
        load_sample(engine, END)
