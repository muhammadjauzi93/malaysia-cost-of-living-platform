import pytest

from col_pipeline import cli


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    assert "col-pipeline" in capsys.readouterr().out


def test_a_command_is_required():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    ("argv", "expected"),
    [(["run"], []), (["run", "--full-refresh"], ["--full-refresh"])],
)
def test_run_delegates_to_the_pipeline(monkeypatch, argv, expected):
    calls = []
    monkeypatch.setattr(cli.pipeline, "run", lambda a: calls.append(a) or 0)
    assert cli.main(argv) == 0
    assert calls == [expected]


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
    assert "run --full-refresh" in cli.format_table([], ["run_id"])


def test_check_reports_missing_configuration(monkeypatch, capsys):
    monkeypatch.setattr(cli.Settings, "from_env", classmethod(lambda cls: _raise()))
    assert cli.check() == 1
    assert "[FAIL] Configuration" in capsys.readouterr().out


def _raise():
    raise RuntimeError("Database settings missing: POSTGRES_PASSWORD")
