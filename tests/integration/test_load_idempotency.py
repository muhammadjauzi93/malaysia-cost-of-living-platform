"""Proves the generic load is idempotent against a real Postgres."""

import pytest
from sqlalchemy import text

from col_pipeline.load import PostgresLoader
from col_pipeline.sources import cpi_state, fuel_prices, fx_rates

pytestmark = pytest.mark.integration


def test_running_twice_changes_nothing(engine, count, fuel_records):
    df = fuel_prices.to_frame(fuel_records)
    loader = PostgresLoader(engine)

    first = loader.upsert(fuel_prices.TABLE, df)
    count_after_first = count("raw.fuel_price_weekly")
    second = loader.upsert(fuel_prices.TABLE, df)

    assert first == {"rows_inserted": len(df), "rows_updated": 0}
    assert second == {"rows_inserted": 0, "rows_updated": 0}
    assert count("raw.fuel_price_weekly") == count_after_first == len(df)


def test_a_revised_price_updates_exactly_one_row(engine, fuel_records):
    loader = PostgresLoader(engine)
    loader.upsert(fuel_prices.TABLE, fuel_prices.to_frame(fuel_records))

    fuel_records[0]["ron95"] = 2.14  # source corrects a published price
    result = loader.upsert(fuel_prices.TABLE, fuel_prices.to_frame(fuel_records))

    assert result == {"rows_inserted": 0, "rows_updated": 1}


def test_loaded_at_only_moves_when_a_value_changes(engine, fuel_records):
    loader = PostgresLoader(engine)
    df = fuel_prices.to_frame(fuel_records)
    loader.upsert(fuel_prices.TABLE, df)

    def stamps():
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT price_date, fuel_type, loaded_at FROM raw.fuel_price_weekly")
            )
            return {(r.price_date, r.fuel_type): r.loaded_at for r in rows}

    before = stamps()
    loader.upsert(fuel_prices.TABLE, df)  # nothing changed
    assert stamps() == before

    fuel_records[0]["ron95"] = 2.14
    loader.upsert(fuel_prices.TABLE, fuel_prices.to_frame(fuel_records))
    after = stamps()
    moved = [k for k in before if after[k] != before[k]]
    assert len(moved) == 1


def test_watermark_is_latest_date_per_table(engine, fuel_records, cpi_records):
    loader = PostgresLoader(engine)
    assert loader.get_watermark(fuel_prices.TABLE) is None
    loader.upsert(fuel_prices.TABLE, fuel_prices.to_frame(fuel_records))
    loader.upsert(cpi_state.TABLE, cpi_state.to_frame(cpi_records))
    assert str(loader.get_watermark(fuel_prices.TABLE)) == "2025-10-02"
    assert str(loader.get_watermark(cpi_state.TABLE)) == "2024-06-01"


def test_cpi_and_fx_round_trip_with_nulls_and_units(engine, cpi_records, fx_records):
    loader = PostgresLoader(engine)
    cpi = cpi_state.to_frame(cpi_records)
    fx_records[0]["rate"]["middle_rate"] = None  # a partly null rate must land as NULL
    fx = fx_rates.to_frame(fx_records)

    assert loader.upsert(cpi_state.TABLE, cpi)["rows_inserted"] == len(cpi)
    assert loader.upsert(fx_rates.TABLE, fx)["rows_inserted"] == len(fx)
    assert loader.upsert(fx_rates.TABLE, fx) == {"rows_inserted": 0, "rows_updated": 0}

    with engine.connect() as conn:
        usd = conn.execute(
            text("SELECT middle_rate, buying_rate FROM raw.fx_rate_daily WHERE currency='USD'")
        ).one()
        jpy_unit = conn.execute(
            text("SELECT unit FROM raw.fx_rate_daily WHERE currency='JPY'")
        ).scalar_one()
    assert usd.middle_rate is None and float(usd.buying_rate) == 4.0735
    assert jpy_unit == 100


def test_database_check_constraints_are_the_last_line_of_defence(engine):
    # Even if every Python check were bypassed, the table itself refuses nonsense.
    import sqlalchemy

    with pytest.raises(sqlalchemy.exc.IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO raw.fx_rate_daily (rate_date, currency, session_code, unit) "
                "VALUES ('2026-01-01', 'USD', '1700', 1)"
            )
        )  # no rate at all
