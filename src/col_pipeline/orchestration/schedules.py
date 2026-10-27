"""When each job runs. All times are Kuala Lumpur time.

The cron expressions encode what we know about each source's publishing rhythm. Where the
exact moment is uncertain, the job runs on several days: ingestion is idempotent, so an extra
run costs a few API calls and changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import dagster as dg

if TYPE_CHECKING:  # not a public Dagster name; only needed for type hints
    from dagster._core.definitions.unresolved_asset_job_definition import (
        UnresolvedAssetJobDefinition,
    )

TIMEZONE = "Asia/Kuala_Lumpur"

# source name -> (cron, why)
CADENCE: dict[str, tuple[str, str]] = {
    # Weekly prices take effect on Thursdays (the dataset's dates are Thursdays in every sample
    # checked). Run Thursday and Friday so a late publication is caught the next morning.
    "fuel_prices": ("0 10 * * 4,5", "Thu and Fri 10:00"),
    # OpenDOSM lists the next CPI release as the 19th at 12:30. Check 14:30 on the 19th to
    # the 25th, because the release day moves around weekends and holidays.
    "cpi_state": ("30 14 19-25 * *", "14:30 daily on the 19th-25th"),
    # Weekdays, after BNM's 17:00 interbank snapshot. BNM does not publish on weekends.
    "fx_rates": ("0 19 * * 1-5", "Mon-Fri 19:00"),
}
MONITOR_CRON = "0 9 * * *"


def build_schedules(
    jobs: dict[str, UnresolvedAssetJobDefinition],
) -> list[dg.ScheduleDefinition]:
    return [
        dg.ScheduleDefinition(
            name=f"{name}_schedule",
            job=job,
            cron_schedule=CADENCE[name][0],
            execution_timezone=TIMEZONE,
            default_status=dg.DefaultScheduleStatus.RUNNING,
            description=CADENCE[name][1],
        )
        for name, job in jobs.items()
        if name in CADENCE
    ]


def build_monitor_schedule(job: UnresolvedAssetJobDefinition) -> dg.ScheduleDefinition:
    return dg.ScheduleDefinition(
        name="quality_monitor_schedule",
        job=job,
        cron_schedule=MONITOR_CRON,
        execution_timezone=TIMEZONE,
        default_status=dg.DefaultScheduleStatus.RUNNING,
        description="Daily 09:00: has any table gone stale? Alerts on breach, not on job failure.",
    )
