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
    expected = {"setup", "up", "reset-db", "full-refresh", "init-db", "probe-bnm", "help"}
    assert expected <= set(tasks.TASKS)


def test_orchestration_tasks_exist():
    assert {"dagster", "materialize", "monitor", "validate"} <= set(tasks.TASKS)


def test_source_list_matches_the_registry():
    from col_pipeline.sources import SOURCES

    assert tasks.SOURCE_NAMES == tuple(SOURCES)


def test_materialize_rejects_an_unknown_source(capsys):
    assert tasks.main(["materialize", "weather"]) == 2
    assert "fuel_prices" in capsys.readouterr().out


def test_dagster_home_is_inside_the_repo_and_absolute():
    env = tasks.dagster_env()
    assert Path(env["DAGSTER_HOME"]).is_absolute()
    assert Path(env["DAGSTER_HOME"]) == ROOT / "dagster_home"


def test_unknown_task_returns_usage_error(capsys):
    assert tasks.main(["nope"]) == 2
    assert "Unknown task" in capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(tasks.TASKS))
def test_every_task_has_a_one_line_description(name):
    assert (tasks.TASKS[name].__doc__ or "").strip()


# ---------- dbt wiring ----------


def test_dbt_tasks_exist():
    assert {"dbt", "dbt-build", "dbt-freshness", "load-sample"} <= set(tasks.TASKS)


def test_dashboard_tasks_exist_and_point_at_the_app():
    assert {"dashboard", "create-dashboard-role"} <= set(tasks.TASKS)
    assert tasks.DASHBOARD_APP.is_file()


def test_read_dotenv_handles_comments_quotes_and_export(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\ufeff# a comment\n"
        "POSTGRES_USER=col_user\n"
        "export POSTGRES_DB=col_platform   # inline comment\n"
        'POSTGRES_PASSWORD="p@ss word#1"\n'
        "\n"
        "NOT_A_PAIR\n",
        encoding="utf-8",
    )
    assert tasks.read_dotenv(env_file) == {
        "POSTGRES_USER": "col_user",
        "POSTGRES_DB": "col_platform",
        "POSTGRES_PASSWORD": "p@ss word#1",  # a '#' inside quotes is part of the value
    }


def test_read_dotenv_missing_file_is_empty(tmp_path):
    assert tasks.read_dotenv(tmp_path / "nope.env") == {}


def test_dbt_env_from_postgres_variables(tmp_path):
    env = tasks.dbt_env(
        {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "pw", "POSTGRES_DB": "d"},
        env_file=tmp_path / "none.env",
    )
    assert env["POSTGRES_HOST"] == "localhost"
    assert env["POSTGRES_PORT"] == "5433"  # the docker-compose host port, as in config.py
    assert (env["POSTGRES_USER"], env["POSTGRES_PASSWORD"], env["POSTGRES_DB"]) == ("u", "pw", "d")


def test_dbt_env_db_url_wins_and_decodes_the_password(tmp_path):
    env = tasks.dbt_env(
        {
            "DB_URL": "postgresql+psycopg2://ci:p%40ss%3Aword@db.example.com:6543/warehouse",
            "POSTGRES_USER": "ignored",
            "POSTGRES_PASSWORD": "ignored",
            "POSTGRES_DB": "ignored",
        },
        env_file=tmp_path / "none.env",
    )
    assert env["POSTGRES_USER"] == "ci"
    assert env["POSTGRES_PASSWORD"] == "p@ss:word"  # percent-encoding undone
    assert (env["POSTGRES_HOST"], env["POSTGRES_PORT"]) == ("db.example.com", "6543")
    assert env["POSTGRES_DB"] == "warehouse"


def test_dbt_env_db_url_without_a_port_means_postgres_default(tmp_path):
    env = tasks.dbt_env(
        {"DB_URL": "postgresql+psycopg2://ci:ci@localhost/col_ci"}, env_file=tmp_path / "none.env"
    )
    assert env["POSTGRES_PORT"] == "5432"


def test_real_environment_beats_the_dotenv_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_USER=from_file\nPOSTGRES_PASSWORD=pw\nPOSTGRES_DB=d\n")
    env = tasks.dbt_env({"POSTGRES_USER": "from_environment"}, env_file=env_file)
    assert env["POSTGRES_USER"] == "from_environment"
    assert env["POSTGRES_PASSWORD"] == "pw"  # still filled in from the file


def test_dbt_env_reports_every_missing_setting(tmp_path):
    with pytest.raises(RuntimeError, match="POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB"):
        tasks.dbt_env({}, env_file=tmp_path / "none.env")


def test_dbt_env_points_dbt_at_the_dbt_folder(tmp_path):
    env = tasks.dbt_env(
        {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p", "POSTGRES_DB": "d"},
        env_file=tmp_path / "none.env",
    )
    assert Path(env["DBT_PROJECT_DIR"]) == ROOT / "dbt"
    assert Path(env["DBT_PROFILES_DIR"]) == ROOT / "dbt"


def test_dbt_dependencies_are_declared_in_pyproject():
    names = " ".join(tasks.group_dependencies("dbt"))
    assert "dbt-core" in names and "dbt-postgres" in names
