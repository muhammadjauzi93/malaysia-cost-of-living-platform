"""Ingestion and the dbt warehouse as one Dagster graph, run for real: Dagster, dbt and Postgres,
with only the fuel API replaced by a fixed record.

These prove the claims of DECISIONS.md #51: an ingestion job refreshes the warehouse downstream
of it in the same run (so the snapshot sees every revision the pipeline loads), and a failing
dbt test fails the run, which is what triggers the failure alert.

They need dbt's packages and manifest. Locally a missing manifest skips them with a hint; in the
CI dbt job, REQUIRE_DBT_TESTS=1 turns that skip into a failure so they can never silently not run.
"""

import dataclasses
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import dagster as dg
import pytest
from sqlalchemy import text

from col_pipeline.orchestration.dbt import DBT_DIR, MANIFEST, build_dbt_assets
from col_pipeline.orchestration.definitions import build_definitions
from col_pipeline.orchestration.resources import PipelineResource
from col_pipeline.sample_data import load_sample
from col_pipeline.sources import SOURCES, fuel_prices

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_dbt():
    if MANIFEST.exists() and (DBT_DIR / "dbt_packages").is_dir():
        return
    message = "dbt manifest or packages missing: run `python tasks.py dbt deps` and `dbt parse`"
    if os.getenv("REQUIRE_DBT_TESTS"):
        pytest.fail(message)
    pytest.skip(message)


WAREHOUSE_SCHEMAS = ("staging", "intermediate", "marts", "reference", "snapshots")


@pytest.fixture(autouse=True)
def empty_warehouse(engine):
    """Start every test from an empty warehouse. The `engine` fixture only empties raw; a
    snapshot is cumulative by design, so without this a re-run sees the previous run's versions.
    Found when the snapshot test failed on its second run with 4 versions where 1 was expected.
    """
    with engine.begin() as conn:
        for schema in WAREHOUSE_SCHEMAS:
            conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def kl_today() -> date:
    return datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()


def run(definitions: dg.Definitions, job: str) -> dg.ExecuteInProcessResult:
    return definitions.resolve_job_def(job).execute_in_process(
        raise_on_error=False, instance=dg.DagsterInstance.ephemeral()
    )


def latest_fuel_record(engine, ron95_delta: float = 0.0) -> dict:
    """The newest weekly announcement in raw, in the API's wide shape, RON95 optionally changed."""
    with engine.connect() as conn:
        latest = conn.execute(
            text("SELECT max(price_date) FROM raw.fuel_price_weekly")
        ).scalar_one()
        prices = dict(
            conn.execute(
                text(
                    "SELECT fuel_type, price_myr FROM raw.fuel_price_weekly "
                    "WHERE price_date = :d AND fuel_type IN ('ron95', 'ron97', 'diesel')"
                ),
                {"d": latest},
            ).all()
        )
    return {
        "date": latest.isoformat(),
        "series_type": "level",
        "ron95": round(float(prices["ron95"]) + ron95_delta, 2),
        "ron97": float(prices["ron97"]),
        "diesel": float(prices["diesel"]),
    }


def build(test_db_url, tmp_path, record: dict) -> dg.Definitions:
    sources = {
        **SOURCES,
        "fuel_prices": dataclasses.replace(
            fuel_prices.SOURCE, fetch=lambda settings, since: [record]
        ),
    }
    pipeline = PipelineResource(db_url=test_db_url, landing_dir=str(tmp_path / "landing"))
    return build_definitions(
        sources=sources, pipeline=pipeline, dbt=build_dbt_assets(sources=sources)
    )


def scalar(engine, sql: str, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar_one()


def test_a_revised_price_flows_from_ingestion_to_snapshot_and_mart_in_one_run(
    engine, test_db_url, tmp_path
):
    load_sample(engine, kl_today())
    record = latest_fuel_record(engine, ron95_delta=0.10)
    defs = build(test_db_url, tmp_path, record)

    bootstrap = run(defs, "dbt_build_job")
    assert bootstrap.success, "the full warehouse build must pass on the sample"
    key = {"d": date.fromisoformat(record["date"])}
    snapshot_rows = (
        "SELECT count(*) FROM snapshots.snap_fuel_prices "
        "WHERE fuel_type = 'ron95' AND price_date = :d"
    )
    assert scalar(engine, snapshot_rows, **key) == 1

    result = run(defs, "fuel_prices_job")
    assert result.success

    refreshed = {e.asset_key.to_user_string() for e in result.get_asset_materialization_events()}
    assert {
        "fuel_prices_raw",
        "staging/stg_fuel_prices",
        "snapshots/snap_fuel_prices",
        "marts/fct_fuel_prices",
        "marts/fct_fuel_vs_transport_cpi",
    } <= refreshed
    assert "staging/stg_cpi_state" not in refreshed  # only what depends on fuel

    # The revision reached the snapshot (old version closed, new one current) and the mart.
    assert scalar(engine, snapshot_rows, **key) == 2
    current = scalar(
        engine,
        "SELECT price_myr FROM snapshots.snap_fuel_prices "
        "WHERE fuel_type = 'ron95' AND price_date = :d AND dbt_valid_to IS NULL",
        **key,
    )
    in_mart = scalar(
        engine,
        "SELECT price_myr FROM marts.fct_fuel_prices WHERE fuel_type = 'ron95' AND price_date = :d",
        **key,
    )
    assert float(current) == float(in_mart) == record["ron95"]

    dbt_checks = [
        e for e in result.get_asset_check_evaluations() if "/" in e.asset_key.to_user_string()
    ]
    assert dbt_checks, "dbt tests should appear as Dagster asset checks"
    assert all(e.passed for e in dbt_checks)


def test_a_failing_dbt_test_fails_the_ingestion_run(engine, test_db_url, tmp_path):
    load_sample(engine, kl_today())
    defs = build(test_db_url, tmp_path, latest_fuel_record(engine))
    assert run(defs, "dbt_build_job").success

    # 'RON95 ' and 'ron95' differ in raw's key but collapse to one row in staging, so the
    # staging uniqueness test must fail.
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO raw.fuel_price_weekly (price_date, fuel_type, price_myr) "
                "SELECT max(price_date), 'RON95 ', 2.05 FROM raw.fuel_price_weekly"
            )
        )

    result = run(defs, "fuel_prices_job")
    assert not result.success, "a failed dbt test must fail the run, which triggers the alert"
    failed = {e.check_name for e in result.get_asset_check_evaluations() if not e.passed}
    assert any("unique_combination_of_columns" in name for name in failed), failed
