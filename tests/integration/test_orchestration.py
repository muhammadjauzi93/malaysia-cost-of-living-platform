"""Run the real Dagster jobs in-process against Postgres, with only the API faked.

These prove the claims the orchestration layer makes: the gate blocks, warnings do not,
retries happen and are audited, staleness is caught, and re-running is safe.
"""

import dataclasses
from datetime import date

import dagster as dg
import pytest

from col_pipeline.orchestration.definitions import build_definitions
from col_pipeline.orchestration.resources import PipelineResource
from col_pipeline.sources import cpi_state, fuel_prices

pytestmark = pytest.mark.integration

FUEL_DATA_DATE = date(2025, 10, 2)  # newest date in the fuel fixture


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """Freshness depends on today's date; pin it to the day after the fixture data."""
    monkeypatch.setattr("col_pipeline.orchestration.assets.kl_today", lambda: date(2025, 10, 3))


class Env:
    def __init__(self, test_db_url, tmp_path):
        self.pipeline = PipelineResource(db_url=test_db_url, landing_dir=str(tmp_path / "landing"))

    def defs(self, sources, **opts):
        return build_definitions(sources=sources, pipeline=self.pipeline, **opts)


@pytest.fixture
def env(test_db_url, tmp_path, engine):  # `engine` truncates the tables first
    return Env(test_db_url, tmp_path)


def fuel_source(records, fetch=None):
    return dataclasses.replace(fuel_prices.SOURCE, fetch=fetch or (lambda settings, since: records))


def run_job(definitions, job, run_config=None):
    return definitions.resolve_job_def(job).execute_in_process(
        run_config=run_config, raise_on_error=False, instance=dg.DagsterInstance.ephemeral()
    )


def checks(result):
    return {
        e.check_name: (e.passed, e.severity.value) for e in result.get_asset_check_evaluations()
    }


def raw_metadata(result):
    (event,) = [
        e
        for e in result.get_asset_materialization_events()
        if e.asset_key.to_user_string() == "fuel_prices_raw"
    ]
    return {k: v.value for k, v in event.materialization.metadata.items()}


def test_happy_path_then_a_rerun_changes_nothing(env, fuel_records, count, runs):
    d = env.defs({"fuel_prices": fuel_source(fuel_records)})

    first = run_job(d, "fuel_prices_job")
    assert first.success
    assert checks(first) == {
        "quality_gate": (True, "ERROR"),
        "quality_warnings": (True, "WARN"),
        "freshness": (True, "ERROR"),
    }
    loaded = count("raw.fuel_price_weekly")
    assert loaded == raw_metadata(first)["rows_inserted"] > 0

    second = run_job(d, "fuel_prices_job")
    assert second.success
    meta = raw_metadata(second)
    assert (meta["rows_inserted"], meta["rows_updated"]) == (0, 0)
    assert count("raw.fuel_price_weekly") == loaded
    assert [r["status"] for r in runs()] == ["success", "success"]


def test_bad_data_stops_at_the_gate_and_nothing_is_loaded(env, fuel_records, count, runs):
    fuel_records[0]["ron95"] = 215.0
    result = run_job(env.defs({"fuel_prices": fuel_source(fuel_records)}), "fuel_prices_job")

    assert not result.success
    assert checks(result)["quality_gate"] == (False, "ERROR")
    assert "fuel_prices_raw" not in {
        e.asset_key.to_user_string() for e in result.get_asset_materialization_events()
    }
    assert count("raw.fuel_price_weekly") == 0
    (run,) = runs()
    assert run["status"] == "failed" and "range:price_myr" in run["error_message"]
    assert [e.step_key for e in result.get_step_failure_events()] == ["fuel_prices_landed"]


def test_a_failed_gate_is_not_retried(env, fuel_records, runs):
    fuel_records[0]["ron95"] = 215.0
    d = env.defs(
        {"fuel_prices": fuel_source(fuel_records)},
        landed_retry=dg.RetryPolicy(max_retries=3, delay=0.01),
    )
    assert not run_job(d, "fuel_prices_job").success
    assert len(runs()) == 1  # one attempt, not four: bad data cannot be fixed by trying again


def test_warnings_are_reported_but_do_not_block(env, cpi_records, count):
    relaxed = dataclasses.replace(cpi_state.SOURCE.rules, min_rows=1, max_age_days=10_000)
    source = dataclasses.replace(
        cpi_state.SOURCE, rules=relaxed, fetch=lambda settings, since: cpi_records
    )
    result = run_job(env.defs({"cpi_state": source}), "cpi_state_job")

    assert result.success
    results = checks(result)
    assert results["quality_gate"] == (True, "ERROR")
    assert results["quality_warnings"] == (False, "WARN")  # month has 4 rows, not 224
    assert count("raw.cpi_state_monthly") == 8


def test_stale_data_fails_the_freshness_check_even_though_the_load_worked(
    env, fuel_records, monkeypatch, count
):
    monkeypatch.setattr("col_pipeline.orchestration.assets.kl_today", lambda: date(2025, 12, 1))
    result = run_job(env.defs({"fuel_prices": fuel_source(fuel_records)}), "fuel_prices_job")

    assert not result.success
    assert checks(result)["freshness"] == (False, "ERROR")
    assert count("raw.fuel_price_weekly") > 0  # the data did load; the *source* is what stalled


def test_monitor_job_alerts_on_an_empty_table_then_passes_once_loaded(env, fuel_records):
    d = env.defs({"fuel_prices": fuel_source(fuel_records)})
    empty = run_job(d, "quality_monitor_job")
    assert not empty.success
    assert "never loaded" in empty.get_asset_check_evaluations()[0].description

    assert run_job(d, "fuel_prices_job").success
    assert run_job(d, "quality_monitor_job").success


def test_monitor_job_notices_staleness_without_any_ingestion(env, fuel_records, monkeypatch):
    d = env.defs({"fuel_prices": fuel_source(fuel_records)})
    assert run_job(d, "fuel_prices_job").success
    monkeypatch.setattr("col_pipeline.orchestration.assets.kl_today", lambda: date(2025, 11, 1))
    stale = run_job(d, "quality_monitor_job")
    assert (
        not stale.success and "30 day(s) old" in stale.get_asset_check_evaluations()[0].description
    )


def test_since_in_run_config_reaches_the_source(env, fuel_records):
    seen = []

    def spy(settings, since):
        seen.append(since)
        return fuel_records

    d = env.defs({"fuel_prices": fuel_source(fuel_records, fetch=spy)})
    cfg = {"ops": {"fuel_prices_landed": {"config": {"since": "2025-09-01"}}}}
    assert run_job(d, "fuel_prices_job", cfg).success
    assert seen == [date(2025, 9, 1)]


def test_conflicting_options_fail_once_without_retrying(env, fuel_records, runs):
    d = env.defs(
        {"fuel_prices": fuel_source(fuel_records)},
        landed_retry=dg.RetryPolicy(max_retries=3, delay=0.01),
    )
    cfg = {"ops": {"fuel_prices_landed": {"config": {"full_refresh": True, "since": "2025-09-01"}}}}
    result = run_job(d, "fuel_prices_job", cfg)
    assert not result.success
    assert runs() == []  # rejected before any run was opened


def test_a_flaky_api_is_retried_and_every_attempt_is_audited(env, fuel_records, runs):
    attempts = []

    def flaky(settings, since):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("API unreachable")
        return fuel_records

    d = env.defs(
        {"fuel_prices": fuel_source(fuel_records, fetch=flaky)},
        landed_retry=dg.RetryPolicy(max_retries=2, delay=0.01),
    )
    result = run_job(d, "fuel_prices_job")

    assert result.success and len(attempts) == 3
    assert [r["status"] for r in runs()] == ["failed", "failed", "success"]


def test_a_load_that_succeeds_on_retry_is_audited_as_success(
    env, fuel_records, count, runs, monkeypatch
):
    from col_pipeline.load import PostgresLoader

    real_upsert = PostgresLoader.upsert
    attempts = []

    def flaky_upsert(self, table, df):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("database connection dropped")
        return real_upsert(self, table, df)

    monkeypatch.setattr(PostgresLoader, "upsert", flaky_upsert)
    d = env.defs(
        {"fuel_prices": fuel_source(fuel_records)},
        raw_retry=dg.RetryPolicy(max_retries=1, delay=0.01),
    )
    result = run_job(d, "fuel_prices_job")

    assert result.success and len(attempts) == 2
    (run,) = runs()  # the retry reuses the landed batch, so it is still one run
    assert run["status"] == "success" and run["error_message"] is None
    assert run["rows_inserted"] == count("raw.fuel_price_weekly") > 0


def test_an_api_that_never_recovers_exhausts_retries_and_the_run_fails(env, fuel_records, runs):
    attempts = []

    def down(settings, since):
        attempts.append(1)
        raise ConnectionError("API unreachable")

    d = env.defs(
        {"fuel_prices": fuel_source(fuel_records, fetch=down)},
        landed_retry=dg.RetryPolicy(max_retries=2, delay=0.01),
    )
    result = run_job(d, "fuel_prices_job")

    assert not result.success and len(attempts) == 3  # first try + 2 retries
    assert {r["status"] for r in runs()} == {"failed"}
