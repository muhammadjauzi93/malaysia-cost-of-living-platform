import pytest

from col_pipeline import cli
from col_pipeline.runner import LoadSummary


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "col-pipeline" in capsys.readouterr().out


def test_a_command_is_required():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_unknown_source_is_rejected_with_the_choices(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["run", "weather"])
    assert exc.value.code == 2
    assert "fuel_prices" in capsys.readouterr().err


def test_full_refresh_and_since_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main(["run", "--full-refresh", "--since", "2026-01-01"])


def test_bad_date_is_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--since", "yesterday"])
    assert "YYYY-MM-DD" in capsys.readouterr().err


@pytest.fixture
def spy(monkeypatch):
    calls = []

    def fake_run_sources(names, **kwargs):
        calls.append((list(names), kwargs))
        return 0

    monkeypatch.setattr(cli, "run_sources", fake_run_sources)
    return calls


def test_run_with_no_sources_means_all_of_them(spy):
    assert cli.main(["run"]) == 0
    assert spy == [([], {"full_refresh": False, "since": None})]


def test_run_passes_named_sources_and_mode_through(spy):
    cli.main(["run", "fx_rates", "cpi_state", "--since", "2026-10-01"])
    names, kwargs = spy[0]
    assert names == ["fx_rates", "cpi_state"]
    assert str(kwargs["since"]) == "2026-10-01"


def test_one_failing_source_does_not_stop_the_others(monkeypatch, capsys, settings):
    seen = []

    def fake_run_source(source, settings, engine, **kwargs):
        seen.append(source.name)
        if source.name == "cpi_state":
            raise RuntimeError("boom")
        return LoadSummary(rows_inserted=2, rows_updated=1, rows_in_frame=5)

    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli.runner, "run_source", fake_run_source)
    monkeypatch.setattr(
        cli, "create_engine", lambda *a, **k: type("E", (), {"dispose": lambda s: None})()
    )

    assert cli.run_sources([]) == 1  # non-zero because one failed
    assert seen == ["fuel_prices", "cpi_state", "fx_rates"]  # but all three ran
    out = capsys.readouterr().out
    assert "[ OK ] fuel_prices: 2 inserted, 1 updated, 2 unchanged" in out
    assert "[FAIL] cpi_state: RuntimeError: boom" in out


def test_format_table_aligns_columns_and_blanks_nulls():
    rows = [
        {"run_id": 12, "status": "success", "error_message": None},
        {"run_id": 3, "status": "failed", "error_message": "boom"},
    ]
    lines = cli.format_table(rows, ["run_id", "status", "error_message"]).splitlines()
    assert lines[0].split() == ["run_id", "status", "error_message"]
    assert lines[2].split() == ["12", "success"]
    assert lines[3].split() == ["3", "failed", "boom"]
    assert lines[2].index("success") == lines[3].index("failed")  # aligned


def test_format_table_truncates_long_errors():
    out = cli.format_table([{"error_message": "x" * 500}], ["error_message"])
    assert "x" * 500 not in out and out.endswith("...")


def test_format_table_with_no_runs_gives_a_hint():
    assert "col-pipeline run" in cli.format_table([], ["run_id"])


def test_sources_command_lists_every_source(capsys):
    assert cli.main(["sources"]) == 0
    out = capsys.readouterr().out
    for name in ("fuel_prices", "cpi_state", "fx_rates", "raw.fx_rate_daily"):
        assert name in out


def test_check_reports_missing_configuration(monkeypatch, capsys):
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: _raise()))
    assert cli.check() == 1
    assert "[FAIL] Configuration" in capsys.readouterr().out


def _raise():
    raise RuntimeError("Database settings missing: POSTGRES_PASSWORD")


def test_last_weekday_before_skips_weekends():
    from datetime import date

    assert cli.last_weekday_before(date(2026, 10, 26)) == date(2026, 10, 23)  # Mon -> Fri
    assert cli.last_weekday_before(date(2026, 10, 27)) == date(2026, 10, 26)  # Tue -> Mon
