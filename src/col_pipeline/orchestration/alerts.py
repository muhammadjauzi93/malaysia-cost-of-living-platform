"""Failure alerts. Slack if SLACK_WEBHOOK_URL is set, otherwise just the Dagster log.

Alerting must never be the thing that breaks: every network problem here is logged and
swallowed, so a Slack outage cannot turn one failed run into two.
"""

import logging
import os

import dagster as dg
import requests
from dotenv import load_dotenv

log = logging.getLogger(__name__)
MAX_ERROR_CHARS = 500


def format_failure_message(job_name: str, run_id: str, error: str | None) -> str:
    detail = (error or "no error message recorded").strip().replace("\n", " ")
    if len(detail) > MAX_ERROR_CHARS:
        detail = detail[: MAX_ERROR_CHARS - 3] + "..."
    return f":rotating_light: Dagster run failed: *{job_name}* (run {run_id[:8]})\n> {detail}"


def send_slack(
    webhook_url: str, text: str, session: requests.Session | None = None, timeout_s: int = 10
) -> bool:
    """Post to a Slack incoming webhook. Returns whether it worked; never raises."""
    http = session or requests.Session()
    try:
        resp = http.post(webhook_url, json={"text": text}, timeout=timeout_s)
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        # type(exc).__name__ only: the exception text can contain the webhook URL (a secret)
        log.warning("Could not send Slack alert: %s", type(exc).__name__)
        return False


def webhook_from_env() -> str | None:
    load_dotenv()
    return os.getenv("SLACK_WEBHOOK_URL") or None


@dg.run_failure_sensor(name="alert_on_failure", default_status=dg.DefaultSensorStatus.RUNNING)
def alert_on_failure(context: dg.RunFailureSensorContext) -> None:
    message = format_failure_message(
        job_name=context.dagster_run.job_name,
        run_id=context.dagster_run.run_id,
        error=context.failure_event.message,
    )
    webhook = webhook_from_env()
    if webhook and send_slack(webhook, message):
        context.log.info("Slack alert sent")
    else:
        context.log.warning("ALERT (Slack not configured or unreachable): %s", message)
