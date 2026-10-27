"""Real HTTP, real Postgres, real clients: only the remote servers are local fakes.

The unit tests prove our logic. These prove the plumbing: the headers and query strings that
actually go over the wire, the real status-code handling, and a retry after a genuine 503.
"""

import dataclasses
from datetime import date

import pytest
from tenacity import wait_none

from col_pipeline import runner
from col_pipeline.config import Settings
from col_pipeline.extract import BNM_ACCEPT, DataGovMyClient, JsonApiClient
from col_pipeline.sources import cpi_state, fuel_prices, fx_rates
from tests.fake_api import FakeApi

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(JsonApiClient.get_json.retry, "wait", wait_none())  # keep the policy
    monkeypatch.setattr(fx_rates.time, "sleep", lambda s: None)


@pytest.fixture
def api(fuel_records, cpi_records, bnm_payload):
    server = FakeApi(
        datasets={"fuelprice": fuel_records, "cpi_state": cpi_records},
        bnm_payload=bnm_payload,
        bnm_holidays={date(2026, 10, 21)},  # a Wednesday with no rates
    ).start()
    yield server
    server.stop()


@pytest.fixture
def settings(api, test_db_url, tmp_path):
    return Settings(
        db_url=test_db_url,
        api_base_url=api.url,
        bnm_base_url=f"{api.url}/public",
        landing_dir=str(tmp_path / "landing"),
    )


def relaxed(source):
    """Fixtures are tiny, so relax only the volume rules; every other rule stays."""
    rules = dataclasses.replace(source.rules, min_rows=1, group_size=None)
    return dataclasses.replace(source, rules=rules)


def test_fuel_incremental_run_sends_the_watermark_over_the_wire(api, settings, engine, count):
    source = relaxed(fuel_prices.SOURCE)
    runner.run_source(source, settings, engine)
    rows = count("raw.fuel_price_weekly")
    again = runner.run_source(source, settings, engine)

    first, second = api.calls("/data-catalogue")
    assert first["query"] == {"id": "fuelprice", "filter": "level@series_type", "sort": "date"}
    assert second["query"]["date_start"] == "2025-09-11@date"  # watermark 2025-10-02 - 21 days
    assert (again.rows_inserted, again.rows_updated) == (0, 0)
    assert count("raw.fuel_price_weekly") == rows


def test_cpi_run_requests_the_cpi_dataset_and_loads_it(api, settings, engine, count):
    summary = runner.run_source(relaxed(cpi_state.SOURCE), settings, engine)
    assert api.calls("/data-catalogue")[0]["query"]["id"] == "cpi_state"
    assert summary.rows_inserted == count("raw.cpi_state_monthly") == 8


def test_daily_fx_run_is_one_request_with_the_vendor_header(api, settings, engine, count):
    summary = runner.run_source(relaxed(fx_rates.SOURCE), settings, engine)

    (call,) = api.calls("/public/")
    assert call["path"] == "/public/exchange-rate"
    assert call["accept"] == BNM_ACCEPT  # what actually crossed the wire
    assert call["query"] == {"session": "1700", "quote": "rm"}
    assert summary.rows_inserted == count("raw.fx_rate_daily") == 5  # five tracked currencies


def test_fx_backfill_tolerates_a_holiday_404_and_is_idempotent(api, settings, engine, count):
    source = relaxed(fx_rates.SOURCE)
    since = date(2026, 10, 19)

    first = runner.run_source(source, settings, engine, since=since)
    # latest (5) + Mon, Tue, Thu x 5 currencies; Wednesday 10-21 was a 404 and is skipped
    assert first.rows_inserted == 5 + 3 * 5
    history = [c for c in api.calls("/public/exchange-rate/") if "/date/" in c["path"]]
    assert len(history) == 4 * 5  # every weekday was asked for, holiday included
    assert not any("/2026-10-24" in c["path"] or "/2026-10-25" in c["path"] for c in history)

    second = runner.run_source(source, settings, engine, since=since)
    assert (second.rows_inserted, second.rows_updated) == (0, 0)
    assert count("raw.fx_rate_daily") == 5 + 3 * 5


def test_a_real_503_is_retried_and_the_run_still_succeeds(api, settings, engine, count):
    api.fail_next["/data-catalogue"] = 2
    runner.run_source(relaxed(fuel_prices.SOURCE), settings, engine)
    assert len(api.calls("/data-catalogue")) == 3  # two 503s, then the real answer
    assert count("raw.fuel_price_weekly") > 0


def test_an_unknown_dataset_is_a_hard_failure_not_a_retry(api, settings, engine, runs):
    def ask_for_a_typo(settings, since):
        return DataGovMyClient(settings.api_base_url).get_catalogue({"id": "no_such_dataset"})

    broken = dataclasses.replace(fuel_prices.SOURCE, fetch=ask_for_a_typo)
    with pytest.raises(Exception, match="404"):
        runner.run_source(broken, settings, engine)
    assert len(api.calls("/data-catalogue")) == 1
    assert runs()[0]["status"] == "failed"
