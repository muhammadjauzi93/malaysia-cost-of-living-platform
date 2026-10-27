"""Whole-source runs (land -> validate -> load) against real Postgres, with a faked API.

These prove the behaviours the platform promises: idempotent re-runs, a quality gate that
really blocks, and an audit trail that tells the truth about every run.
"""

import dataclasses

import pytest

from col_pipeline import runner
from col_pipeline.errors import QualityGateError
from col_pipeline.sources import cpi_state, fuel_prices, fx_rates

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(test_db_url, tmp_path):
    from col_pipeline.config import Settings

    return Settings(db_url=test_db_url, landing_dir=str(tmp_path / "landing"))


def with_records(source, records):
    """The same source, but its API call returns `records`."""
    return dataclasses.replace(source, fetch=lambda settings, since: records)


@pytest.fixture
def sources_and_data(fuel_records, cpi_records, fx_records):
    return {
        "fuel_prices": (fuel_prices.SOURCE, fuel_records),
        "cpi_state": (cpi_state.SOURCE, cpi_records),
        "fx_rates": (fx_rates.SOURCE, fx_records),
    }


@pytest.mark.parametrize("name", ["fuel_prices", "cpi_state", "fx_rates"])
def test_every_source_is_idempotent_end_to_end(
    name, sources_and_data, settings, engine, count, runs
):
    source, records = sources_and_data[name]
    # The fixtures are tiny, so judge them against a relaxed volume rule. Everything else
    # (ranges, keys, ordering) stays as in production.
    relaxed = dataclasses.replace(source.rules, min_rows=1, group_size=None)
    source = dataclasses.replace(with_records(source, records), rules=relaxed)

    first = runner.run_source(source, settings, engine)
    rows = count(source.table.qualified)
    second = runner.run_source(source, settings, engine)

    assert first.rows_inserted == rows > 0
    assert (second.rows_inserted, second.rows_updated) == (0, 0)
    assert count(source.table.qualified) == rows

    log = runs()
    assert [r["status"] for r in log] == ["success", "success"]
    assert {r["pipeline_name"] for r in log} == {name}
    assert log[0]["rows_inserted"] == rows and log[1]["rows_inserted"] == 0
    assert log[0]["landed_file"]


def test_bad_data_is_blocked_before_it_reaches_the_table(
    settings, engine, count, runs, fuel_records
):
    fuel_records[0]["ron95"] = 215.0  # sen instead of ringgit
    source = with_records(fuel_prices.SOURCE, fuel_records)

    with pytest.raises(QualityGateError, match="range:price_myr"):
        runner.run_source(source, settings, engine)

    assert count("raw.fuel_price_weekly") == 0  # nothing was loaded, not even the good rows
    (run,) = runs()
    assert run["status"] == "failed"
    assert "Quality gate" in run["error_message"] and "range:price_myr" in run["error_message"]
    assert run["rows_inserted"] is None


def test_a_structurally_broken_payload_is_blocked_and_recorded(settings, engine, count, runs):
    source = with_records(cpi_state.SOURCE, [{"unexpected": "shape"}])
    with pytest.raises(QualityGateError, match="Missing required columns"):
        runner.run_source(source, settings, engine)
    assert count("raw.cpi_state_monthly") == 0
    assert runs()[0]["status"] == "failed"
    assert "Data contract" in runs()[0]["error_message"]


def test_a_failed_fetch_leaves_a_failed_run_and_no_landing_file(settings, engine, runs, tmp_path):
    def boom(settings, since):
        raise ConnectionError("API unreachable")

    source = dataclasses.replace(fuel_prices.SOURCE, fetch=boom)
    with pytest.raises(ConnectionError):
        runner.run_source(source, settings, engine)

    (run,) = runs()
    assert run["status"] == "failed" and "API unreachable" in run["error_message"]
    assert run["landed_file"] is None
    assert not (tmp_path / "landing").exists()


def test_a_finished_run_cannot_be_overwritten(engine, runs):
    from col_pipeline.load import RunLogger

    logger = RunLogger(engine)
    run_id = logger.start("demo", None)
    logger.succeed(run_id, 1, 1, 0, "f.json")
    logger.fail(run_id, "late failure that must be ignored")
    assert runs()[0]["status"] == "success" and runs()[0]["error_message"] is None


def test_a_retried_run_that_succeeds_is_recorded_as_success(engine, runs):
    """Dagster retries a failed load with the same run_id; the log must follow the retry."""
    from col_pipeline.load import RunLogger

    logger = RunLogger(engine)
    run_id = logger.start("demo", None)
    logger.fail(run_id, "OperationalError: connection dropped")
    logger.succeed(run_id, 5, 5, 0, "f.json")

    (run,) = runs()
    assert run["status"] == "success" and run["error_message"] is None
    assert (run["rows_extracted"], run["rows_inserted"], run["landed_file"]) == (5, 5, "f.json")


def test_the_gate_marks_the_run_failed_even_when_load_is_never_called(
    settings, engine, runs, fuel_records
):
    """This is what the Dagster blocking check relies on: validate() alone closes the run."""
    fuel_records[0]["ron95"] = 215.0
    source = with_records(fuel_prices.SOURCE, fuel_records)
    batch = runner.land(source, settings, engine)
    assert runs()[0]["status"] == "running"

    gate = runner.validate(source, batch, engine)

    assert not gate.passed
    assert runs()[0]["status"] == "failed"


def test_load_refuses_a_batch_that_never_passed_the_gate(settings, engine, count, fuel_records):
    fuel_records[0]["ron95"] = 215.0
    source = with_records(fuel_prices.SOURCE, fuel_records)
    batch = runner.land(source, settings, engine)
    with pytest.raises(QualityGateError):
        runner.load_batch(source, engine, batch)  # no validate() beforehand
    assert count("raw.fuel_price_weekly") == 0


def test_incremental_run_asks_for_data_behind_the_watermark(settings, engine, fuel_records):
    seen = []

    def spy(settings, since):
        seen.append(since)
        return fuel_records

    source = dataclasses.replace(fuel_prices.SOURCE, fetch=spy)
    runner.run_source(source, settings, engine)  # empty table: default history
    runner.run_source(source, settings, engine)  # watermark is 2025-10-02, lookback 21 days
    runner.run_source(source, settings, engine, full_refresh=True)

    assert [str(s) for s in seen] == ["None", "2025-09-11", "None"]


def test_a_run_that_died_is_closed_by_the_next_run_of_the_same_source(
    settings, engine, runs, fuel_records
):
    from sqlalchemy import text

    from col_pipeline.load import RunLogger

    old = RunLogger(engine).start("fuel_prices", None)  # started, then the process was killed
    recent = RunLogger(engine).start("fuel_prices", None)  # could be a genuine concurrent run
    other = RunLogger(engine).start("cpi_state", None)  # a different source is never touched
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ops.pipeline_runs SET started_at = now() - interval '7 hours' "
                 "WHERE run_id IN (:a, :b)"),
            {"a": old, "b": other},
        )  # fmt: skip

    runner.run_source(with_records(fuel_prices.SOURCE, fuel_records), settings, engine)

    by_id = {r["run_id"]: r for r in runs()}
    assert by_id[old]["status"] == "failed" and "Abandoned" in by_id[old]["error_message"]
    assert by_id[recent]["status"] == "running"
    assert by_id[other]["status"] == "running"
