from datetime import date

from col_pipeline.main import resolve_since


def test_empty_table_pulls_full_history():
    assert resolve_since(None, lookback_days=21, full_refresh=False) is None


def test_full_refresh_ignores_watermark():
    assert resolve_since(date(2026, 10, 1), lookback_days=21, full_refresh=True) is None


def test_incremental_looks_back_from_watermark():
    since = resolve_since(date(2026, 10, 1), lookback_days=21, full_refresh=False)
    assert since == date(2026, 9, 10)
