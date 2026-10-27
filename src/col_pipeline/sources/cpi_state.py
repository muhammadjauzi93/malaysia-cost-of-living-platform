"""Monthly CPI by state and division from OpenDOSM (dataset `cpi_state`).

Already long-format (one row per state, month and division), so the transform is mostly
validation. Field names come from the dataset's published variable definitions:
state, date (always the 1st), division ('01'-'13' or 'overall'), index (base 2010 = 100).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from col_pipeline.config import Settings
from col_pipeline.errors import DataContractError
from col_pipeline.extract import DataGovMyClient
from col_pipeline.sources.base import QualityRules, Source, TableSpec

log = logging.getLogger(__name__)

CPI_DATASET_ID = "cpi_state"
REQUIRED_COLUMNS = {"state", "date", "division", "index"}
OUTPUT_COLUMNS = ["state", "month_date", "division", "cpi_index"]

STATES_PER_MONTH = 16
DIVISIONS_PER_STATE = 14  # 13 COICOP divisions + 'overall'

TABLE = TableSpec(
    schema="raw",
    table="cpi_state_monthly",
    key_columns=("state", "month_date", "division"),
    value_columns=("cpi_index",),
    date_column="month_date",
)

# - min_rows: at least one month's worth of rows (16 states x 14 divisions = 224), less slack.
# - cpi_index bounds are wide on purpose (base 2010 = 100; real values sit well inside).
# - max_age_days=90: month M is published around the 19th of M+1, so the newest month_date can
#   legitimately be ~80 days old right before a release.
RULES = QualityRules(
    min_rows=200,
    max_age_days=90,
    value_ranges={"cpi_index": (30.0, 300.0)},
    group_size=("month_date", STATES_PER_MONTH * DIVISIONS_PER_STATE),
)


def cpi_params(since: date | None = None) -> dict[str, str]:
    params = {"id": CPI_DATASET_ID, "sort": "date"}
    if since is not None:
        params["date_start"] = f"{since.isoformat()}@date"
    return params


def fetch(settings: Settings, since: date | None) -> list[dict[str, Any]]:
    client = DataGovMyClient(
        base_url=settings.api_base_url,
        timeout_s=settings.request_timeout_s,
        token=settings.api_token,
    )
    log.info("Requesting dataset=%s since=%s", CPI_DATASET_ID, since or "beginning")
    records = client.get_catalogue(cpi_params(since))
    log.info("Received %d records", len(records))
    return records


def _normalise_division(value: object) -> str:
    """'7' -> '07'; 'Overall ' -> 'overall'. Divisions are codes, so they stay text."""
    text = str(value).strip().lower()
    return text.zfill(2) if text.isdigit() else text


def to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        raise DataContractError("Extract returned zero records")

    df = pd.DataFrame.from_records(records)
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise DataContractError(f"Missing required columns: {sorted(missing)}")

    out = pd.DataFrame(
        {
            "state": df["state"].astype("string").str.strip(),
            "month_date": pd.to_datetime(df["date"], errors="coerce").dt.date,
            "division": df["division"].map(_normalise_division),
            "cpi_index": pd.to_numeric(df["index"], errors="coerce").round(3),
        }
    )
    out = out.dropna(subset=OUTPUT_COLUMNS)

    # The dataset says dates are the 1st of the month. If that ever changes, our monthly grain
    # (and every join on month_date) is wrong, so stop instead of guessing.
    off_grain = out[[d.day != 1 for d in out["month_date"]]]
    if not off_grain.empty:
        example = off_grain["month_date"].iloc[0]
        raise DataContractError(
            f"{len(off_grain)} dates are not the 1st of a month, e.g. {example}"
        )

    before = len(out)
    out = out.drop_duplicates(subset=["state", "month_date", "division"], keep="last")
    if dropped := before - len(out):
        log.warning("Dropped %d duplicate (state, month_date, division) rows", dropped)

    out = out.sort_values(["month_date", "state", "division"]).reset_index(drop=True)
    if out.empty:
        raise DataContractError("Transform produced zero rows")
    out["state"] = out["state"].astype(object)  # plain Python str, friendly to drivers

    log.info(
        "Transformed %d CPI rows: %d states, months %s to %s",
        len(out),
        out["state"].nunique(),
        out["month_date"].min(),
        out["month_date"].max(),
    )
    return out[OUTPUT_COLUMNS]


SOURCE = Source(
    name="cpi_state",
    description="Monthly CPI by state and division (OpenDOSM)",
    dataset_id=CPI_DATASET_ID,
    table=TABLE,
    rules=RULES,
    fetch=fetch,
    to_frame=to_frame,
    lookback_days=92,  # re-read about three months in case DOSM revises a recent index
)
