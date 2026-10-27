from datetime import date

import pytest

from col_pipeline.runner import resolve_since
from col_pipeline.sources import cpi_state, fuel_prices, fx_rates

WATERMARK = date(2026, 10, 1)


def test_empty_table_pulls_the_default_history():
    assert resolve_since(fuel_prices.SOURCE, None) is None


def test_full_refresh_ignores_the_watermark():
    assert resolve_since(fuel_prices.SOURCE, WATERMARK, full_refresh=True) is None


def test_incremental_looks_back_by_the_sources_own_lookback():
    assert resolve_since(fuel_prices.SOURCE, WATERMARK) == date(2026, 9, 10)  # 21 days
    assert resolve_since(cpi_state.SOURCE, WATERMARK) == date(2026, 7, 1)  # 92 days


def test_latest_only_sources_ignore_the_watermark_until_asked():
    assert resolve_since(fx_rates.SOURCE, WATERMARK) is None


def test_an_explicit_since_always_wins():
    since = date(2026, 1, 1)
    assert resolve_since(fx_rates.SOURCE, WATERMARK, explicit_since=since) == since
    assert resolve_since(fuel_prices.SOURCE, WATERMARK, explicit_since=since) == since


@pytest.mark.parametrize("source", [fuel_prices.SOURCE, cpi_state.SOURCE, fx_rates.SOURCE])
def test_each_source_uses_its_own_table_name_in_the_run_log(source):
    assert source.name in {"fuel_prices", "cpi_state", "fx_rates"}
