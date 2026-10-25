"""tasks.py lives outside the package, so load it by path."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tasks", ROOT / "tasks.py")
tasks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tasks)


def test_windows_venv_python_path():
    # The original bug: .venv/bin/python does not exist on Windows.
    assert tasks.venv_python_path(Path(".venv"), os_name="nt") == Path(".venv/Scripts/python.exe")


def test_unix_venv_python_path():
    assert tasks.venv_python_path(Path(".venv"), os_name="posix") == Path(".venv/bin/python")


def test_dev_dependencies_come_from_pyproject():
    deps = tasks.dev_dependencies()
    assert any(d.startswith("pytest") for d in deps)
    assert any(d.startswith("ruff") for d in deps)


def test_task_names_are_dash_case():
    assert {"setup", "up", "reset-db", "full-refresh", "help"} <= set(tasks.TASKS)


def test_unknown_task_returns_usage_error(capsys):
    assert tasks.main(["nope"]) == 2
    assert "Unknown task" in capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(tasks.TASKS))
def test_every_task_has_a_one_line_description(name):
    assert (tasks.TASKS[name].__doc__ or "").strip()
