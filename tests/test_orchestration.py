"""Orchestration tests that need no database: structure, schedules, alerts, config."""

from datetime import datetime
from zoneinfo import ZoneInfo

import dagster as dg
import pytest
import requests
from croniter import croniter

from col_pipeline.orchestration import alerts
from col_pipeline.orchestration.assets import IngestConfig, _parse_since
from col_pipeline.orchestration.definitions import build_definitions
from col_pipeline.orchestration.resources import PipelineResource
from col_pipeline.orchestration.schedules import CADENCE, MONITOR_CRON, TIMEZONE
from col_pipeline.sources import SOURCES

# The ingestion definitions, built directly. The production code location (definitions.defs)
# is lazy and also contains the dbt warehouse; that wiring is tested in test_dbt_orchestration.py.
defs = build_definitions()

KL = ZoneInfo(TIMEZONE)


def next_fires(cron: str, n: int = 60, start=datetime(2026, 10, 1, 0, 0, tzinfo=KL)):
    it = croniter(cron, start)
    return [it.get_next(datetime) for _ in range(n)]


# ---- structure ----


def test_every_source_gets_two_assets_a_job_a_schedule_and_a_freshness_check():
    for name in SOURCES:
        assert defs.get_assets_def(f"{name}_landed")
        assert defs.get_assets_def(f"{name}_raw")
        assert defs.resolve_job_def(f"{name}_job")
        assert defs.resolve_schedule_def(f"{name}_schedule")
    checks = {k.name for a in defs.asset_checks for k in a.check_keys}
    assert checks == {"freshness"}


def test_the_raw_load_depends_on_the_landed_asset():
    raw = defs.get_assets_def("fuel_prices_raw")
    assert {k.to_user_string() for k in raw.dependency_keys} == {"fuel_prices_landed"}


def test_the_quality_gate_is_blocking_and_the_warnings_check_is_not():
    specs = {s.key.name: s.blocking for s in defs.get_assets_def("cpi_state_landed").check_specs}
    assert specs == {"quality_gate": True, "quality_warnings": False}


def test_a_failure_sensor_is_registered_and_on_by_default():
    sensor = defs.resolve_sensor_def("alert_on_failure")
    assert sensor.default_status == dg.DefaultSensorStatus.RUNNING


def test_monitor_job_runs_only_the_freshness_checks():
    job = defs.resolve_job_def("quality_monitor_job")
    assert sorted(n.name for n in job.nodes) == [
        "cpi_state_raw_freshness",
        "fuel_prices_raw_freshness",
        "fx_rates_raw_freshness",
    ]


def test_retry_policies_use_two_time_scales():
    landed = defs.get_assets_def("fuel_prices_landed").op.retry_policy
    raw = defs.get_assets_def("fuel_prices_raw").op.retry_policy
    assert (landed.max_retries, landed.delay) == (2, 300)  # API trouble: minutes apart
    assert (raw.max_retries, raw.delay) == (3, 30)  # database blip: seconds apart
    assert landed.backoff == raw.backoff == dg.Backoff.EXPONENTIAL


# ---- schedules ----


def test_all_schedules_run_in_kuala_lumpur_time_and_start_on():
    for s in defs.schedules:
        assert s.execution_timezone == TIMEZONE
        assert s.default_status == dg.DefaultScheduleStatus.RUNNING


def test_fuel_runs_thursday_and_friday_mornings_only():
    fires = next_fires(CADENCE["fuel_prices"][0])
    assert {f.weekday() for f in fires} == {3, 4}  # Mon=0 -> Thu=3, Fri=4
    assert {f.hour for f in fires} == {10}


def test_cpi_checks_only_between_the_19th_and_25th():
    fires = next_fires(CADENCE["cpi_state"][0])
    assert {f.day for f in fires} == set(range(19, 26))
    assert len({(f.year, f.month) for f in fires}) >= 8  # every month, not just one


def test_fx_runs_on_weekday_evenings_never_on_weekends():
    fires = next_fires(CADENCE["fx_rates"][0])
    assert all(f.weekday() < 5 and f.hour == 19 for f in fires)
    assert {f.weekday() for f in fires} == {0, 1, 2, 3, 4}


def test_monitor_runs_every_morning():
    fires = next_fires(MONITOR_CRON, n=10)
    assert [f.hour for f in fires] == [9] * 10
    assert (fires[1] - fires[0]).days == 1


# ---- config ----


def test_since_and_full_refresh_together_is_rejected_without_retries():
    with pytest.raises(dg.Failure, match="either") as exc:
        _parse_since(IngestConfig(full_refresh=True, since="2026-01-01"))
    assert exc.value.allow_retries is False


def test_bad_since_is_rejected_without_retries():
    with pytest.raises(dg.Failure, match="YYYY-MM-DD") as exc:
        _parse_since(IngestConfig(since="yesterday"))
    assert exc.value.allow_retries is False


def test_defaults_mean_a_normal_incremental_run():
    assert _parse_since(IngestConfig()) is None
    assert str(_parse_since(IngestConfig(since="2026-10-01"))) == "2026-10-01"


def test_resource_overrides_beat_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_URL", "postgresql+psycopg2://env:env@localhost:1/env")
    res = PipelineResource(
        db_url="postgresql+psycopg2://t:t@localhost:2/t", landing_dir=str(tmp_path)
    )
    settings = res.settings()
    assert settings.db_url.endswith("localhost:2/t") and settings.landing_dir == str(tmp_path)
    assert PipelineResource().settings().db_url.endswith("localhost:1/env")


def test_definitions_can_be_built_from_a_subset_of_sources():
    sub = build_definitions(sources={"fuel_prices": SOURCES["fuel_prices"]})
    assert {s.name for s in sub.schedules} == {"fuel_prices_schedule", "quality_monitor_schedule"}


# ---- alerts ----


def test_failure_message_names_the_job_and_a_short_run_id():
    msg = alerts.format_failure_message("fx_rates_job", "abcdef12-3456", "boom")
    assert "fx_rates_job" in msg and "abcdef12" in msg and "3456" not in msg and "boom" in msg


def test_long_errors_are_truncated_and_flattened():
    msg = alerts.format_failure_message("j", "r" * 36, "line1\nline2 " + "x" * 2000)
    assert len(msg) < 700 and "\nline2" not in msg and msg.endswith("...")


def test_missing_error_text_is_explicit():
    assert "no error message" in alerts.format_failure_message("j", "r" * 36, None)


class FakeHttp:
    def __init__(self, status=200, raises=None):
        self.status, self.raises, self.sent = status, raises, []

    def post(self, url, json=None, timeout=None):
        self.sent.append((url, json, timeout))
        if self.raises:
            raise self.raises
        resp = requests.Response()
        resp.status_code = self.status
        return resp


def test_slack_post_sends_text_with_a_timeout():
    http = FakeHttp()
    assert alerts.send_slack("https://hooks.example/secret", "hi", session=http) is True
    assert http.sent == [("https://hooks.example/secret", {"text": "hi"}, 10)]


@pytest.mark.parametrize(
    "http", [FakeHttp(status=500), FakeHttp(raises=requests.ConnectionError("down"))]
)
def test_slack_trouble_never_raises(http):
    assert alerts.send_slack("https://hooks.example/secret", "hi", session=http) is False


def test_slack_failure_log_does_not_leak_the_webhook_url(caplog):
    http = FakeHttp(raises=requests.ConnectionError("cannot reach https://hooks.example/secret"))
    with caplog.at_level("WARNING"):
        alerts.send_slack("https://hooks.example/secret", "hi", session=http)
    assert "ConnectionError" in caplog.text and "secret" not in caplog.text


def test_webhook_is_optional(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(alerts, "load_dotenv", lambda: None)
    assert alerts.webhook_from_env() is None
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    assert alerts.webhook_from_env() == "https://hooks.example/x"
