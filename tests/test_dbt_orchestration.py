"""Unit tests for the dbt-in-Dagster wiring (orchestration/dbt.py). No database needed.

The graph tests read dbt's manifest; without one they skip locally and fail in the CI dbt job
(REQUIRE_DBT_TESTS=1), like the integration tests.
"""

import os

import dagster as dg
import pytest

from col_pipeline.orchestration import dbt as dbt_orch
from col_pipeline.orchestration.definitions import build_definitions
from col_pipeline.sources import SOURCES


def test_postgres_env_decodes_an_escaped_password():
    env = dbt_orch.postgres_env("postgresql+psycopg2://col:p%40ss%3Aw%2Frd@db.local:6543/wh")
    assert env == {
        "POSTGRES_HOST": "db.local",
        "POSTGRES_PORT": "6543",
        "POSTGRES_USER": "col",
        "POSTGRES_PASSWORD": "p@ss:w/rd",
        "POSTGRES_DB": "wh",
    }


def test_postgres_env_defaults_the_port_to_postgres_own():
    assert dbt_orch.postgres_env("postgresql+psycopg2://u:p@localhost/d")["POSTGRES_PORT"] == "5432"


def test_temporary_environ_sets_then_restores_and_removes(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "before")
    monkeypatch.delenv("POSTGRES_DB", raising=False)
    with dbt_orch.temporary_environ({"POSTGRES_USER": "during", "POSTGRES_DB": "x"}):
        assert os.environ["POSTGRES_USER"] == "during"
        assert os.environ["POSTGRES_DB"] == "x"
    assert os.environ["POSTGRES_USER"] == "before"
    assert "POSTGRES_DB" not in os.environ


def test_temporary_environ_restores_even_when_the_block_fails(monkeypatch):
    monkeypatch.setenv("POSTGRES_USER", "before")
    with pytest.raises(RuntimeError):
        with dbt_orch.temporary_environ({"POSTGRES_USER": "during"}):
            raise RuntimeError("dbt failed to start")
    assert os.environ["POSTGRES_USER"] == "before"


def test_every_raw_table_maps_to_the_ingestion_asset_that_writes_it():
    assert dbt_orch.raw_asset_keys(SOURCES) == {
        "raw.fuel_price_weekly": "fuel_prices_raw",
        "raw.cpi_state_monthly": "cpi_state_raw",
        "raw.fx_rate_daily": "fx_rates_raw",
    }


def test_a_missing_manifest_is_a_clear_error(tmp_path):
    with pytest.raises(dbt_orch.ManifestMissingError, match="dbt parse"):
        dbt_orch.build_dbt_assets(manifest=tmp_path / "manifest.json")


# ---------- the real graph, from the real manifest ----------


@pytest.fixture
def graph():
    if not dbt_orch.MANIFEST.exists():
        message = "dbt manifest missing: run `python tasks.py dbt parse`"
        if os.getenv("REQUIRE_DBT_TESTS"):
            pytest.fail(message)
        pytest.skip(message)
    defs = build_definitions(dbt=dbt_orch.build_dbt_assets())
    return defs.get_repository_def()


def test_dbt_sources_connect_to_the_ingestion_assets(graph):
    g = graph.asset_graph
    for raw, staging in [
        ("fuel_prices_raw", "staging/stg_fuel_prices"),
        ("cpi_state_raw", "staging/stg_cpi_state"),
        ("fx_rates_raw", "staging/stg_fx_rates"),
    ]:
        assert dg.AssetKey.from_user_string(staging) in g.get(dg.AssetKey(raw)).child_keys
    stray = [
        k.to_user_string() for k in g.get_all_asset_keys() if k.to_user_string().startswith("raw/")
    ]
    assert stray == [], "a dbt source became its own asset instead of joining the ingestion graph"


def test_the_fuel_job_refreshes_the_snapshot_and_the_dashboard_mart(graph):
    selected = {
        k.to_user_string() for k in graph.get_job("fuel_prices_job").asset_layer.selected_asset_keys
    }
    assert {"fuel_prices_landed", "fuel_prices_raw", "snapshots/snap_fuel_prices",
            "marts/fct_fuel_vs_transport_cpi"} <= selected  # fmt: skip
    assert "staging/stg_cpi_state" not in selected


def test_every_ingestion_job_refreshes_the_data_health_mart(graph):
    # The dashboard's synthetic label and last-load times must change in the run that changed
    # the data, not at the next full build (DECISIONS.md #62).
    for job in ("fuel_prices_job", "cpi_state_job", "fx_rates_job"):
        selected = {k.to_user_string() for k in graph.get_job(job).asset_layer.selected_asset_keys}
        assert "marts/mart_data_health" in selected, job


def test_the_run_log_is_an_external_table_not_a_stray_asset(graph):
    run_log = graph.asset_graph.get(dbt_orch.RUN_LOG.key)
    assert run_log.group_name == "pipeline_metadata"
    assert not run_log.is_materializable


def test_dbt_assets_are_grouped_by_layer_and_the_build_job_exists(graph):
    g = graph.asset_graph
    groups = {g.get(k).group_name for k in g.get_all_asset_keys() if "/" in k.to_user_string()}
    assert groups == set(dbt_orch.GROUPS.values())
    assert graph.has_job(dbt_orch.DBT_BUILD_JOB)


def test_without_dbt_the_ingestion_jobs_are_unchanged():
    repo = build_definitions().get_repository_def()
    selected = {
        k.to_user_string() for k in repo.get_job("fuel_prices_job").asset_layer.selected_asset_keys
    }
    assert selected == {"fuel_prices_landed", "fuel_prices_raw"}
    assert not repo.has_job(dbt_orch.DBT_BUILD_JOB)


def test_schedules_still_drive_the_dbt_aware_jobs(graph):
    for name in SOURCES:
        schedule = graph.get_schedule_def(f"{name}_schedule")
        assert schedule.job_name == f"{name}_job"
        assert schedule.execution_timezone == "Asia/Kuala_Lumpur"


def test_dbt_is_found_without_relying_on_path(monkeypatch):
    """Regression: `python tasks.py` runs the venv's Python without activating the venv, so the
    folder holding the dbt program is not on PATH. dagster-dbt's default ('dbt' on PATH) failed
    on the first real run on Windows. The resource must find dbt beside the running Python."""
    monkeypatch.setenv("PATH", "")
    resource = dbt_orch.dbt_resource()  # raised a ValidationError before the fix
    assert os.path.isabs(resource.dbt_executable)
    assert os.path.exists(resource.dbt_executable)
