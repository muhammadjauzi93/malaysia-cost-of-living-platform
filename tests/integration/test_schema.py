import pytest

from col_pipeline import cli

pytestmark = pytest.mark.integration


def test_init_db_is_repeatable_and_check_agrees(test_db_url, monkeypatch, capsys):
    monkeypatch.setenv("DB_URL", test_db_url)
    assert cli.init_db() == 0
    assert cli.init_db() == 0  # IF NOT EXISTS everywhere
    assert cli.check() == 0
    out = capsys.readouterr().out
    assert "[ OK ] Schema present" in out and "raw.fx_rate_daily" in out


def test_check_names_the_tables_it_cannot_find(test_db_url, monkeypatch, capsys, engine):
    from sqlalchemy import text

    monkeypatch.setenv("DB_URL", test_db_url)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE raw.fx_rate_daily RENAME TO fx_rate_daily_moved"))
    try:
        assert cli.check() == 1
        out = capsys.readouterr().out
        assert "Missing tables: raw.fx_rate_daily" in out and "init-db" in out
    finally:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE raw.fx_rate_daily_moved RENAME TO fx_rate_daily"))
