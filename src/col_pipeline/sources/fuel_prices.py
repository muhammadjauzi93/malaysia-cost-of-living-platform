"""Weekly Malaysian fuel prices from data.gov.my (dataset `fuelprice`).

Phase 1's logic, unchanged, now packaged as a `Source`. See DECISIONS.md #1-#3 for why the
data is stored long, why only `level` rows are loaded, and why the lookback is 21 days.
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

FUELPRICE_DATASET_ID = "fuelprice"
ID_COLUMNS = {"date", "series_type"}  # everything else the API sends is a fuel price
OUTPUT_COLUMNS = ["price_date", "fuel_type", "price_myr"]

TABLE = TableSpec(
    schema="raw",
    table="fuel_price_weekly",
    key_columns=("price_date", "fuel_type"),
    value_columns=("price_myr",),
    date_column="price_date",
)

# Plausible MYR-per-litre bounds. Real prices have ranged roughly RM1.50 to RM5. A value
# outside [0.50, 10.00] means a unit slip (sen instead of ringgit) or a broken source.
# max_age_days=10: prices change weekly, plus a few days of publication lag.
RULES = QualityRules(min_rows=3, max_age_days=10, value_ranges={"price_myr": (0.50, 10.00)})


def fuel_price_params(since: date | None = None) -> dict[str, str]:
    """Query params for the fuelprice dataset. Pure function, so it is trivially testable."""
    params = {
        "id": FUELPRICE_DATASET_ID,
        "filter": "level@series_type",  # prices only; weekly changes are derivable later
        "sort": "date",
    }
    if since is not None:
        params["date_start"] = f"{since.isoformat()}@date"
    return params


def fetch(settings: Settings, since: date | None) -> list[dict[str, Any]]:
    client = DataGovMyClient(
        base_url=settings.api_base_url,
        timeout_s=settings.request_timeout_s,
        token=settings.api_token,
    )
    log.info("Requesting dataset=%s since=%s", FUELPRICE_DATASET_ID, since or "beginning")
    records = client.get_catalogue(fuel_price_params(since))
    log.info("Received %d records", len(records))
    return records


def to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Reshape wide fuel-price records into one row per (price_date, fuel_type)."""
    if not records:
        raise DataContractError("Extract returned zero records")

    df = pd.DataFrame.from_records(records)
    missing = ID_COLUMNS - set(df.columns)
    if missing:
        raise DataContractError(f"Missing required columns: {sorted(missing)}")

    # Belt and braces: the API filter should already do this, but never trust a filter blindly.
    df = df[df["series_type"] == "level"]

    price_columns = sorted(c for c in df.columns if c not in ID_COLUMNS)
    if not price_columns:
        raise DataContractError("No fuel price columns found")

    long = df.melt(
        id_vars=["date"],
        value_vars=price_columns,
        var_name="fuel_type",
        value_name="price_myr",
    )
    long["price_date"] = pd.to_datetime(long["date"], errors="coerce").dt.date
    long["price_myr"] = pd.to_numeric(long["price_myr"], errors="coerce").round(2)

    # A null price means "this fuel type did not exist yet", e.g. BUDI95 before Sep 2025.
    long = long.dropna(subset=["price_date", "price_myr"])

    before = len(long)
    long = long.drop_duplicates(subset=["price_date", "fuel_type"], keep="last")
    if dropped := before - len(long):
        log.warning("Dropped %d duplicate (price_date, fuel_type) rows", dropped)

    long = long.sort_values(["price_date", "fuel_type"]).reset_index(drop=True)
    if long.empty:
        raise DataContractError("Transform produced zero rows")

    log.info(
        "Transformed %d rows across fuel types: %s",
        len(long),
        ", ".join(sorted(long["fuel_type"].unique())),
    )
    return long[OUTPUT_COLUMNS]


SOURCE = Source(
    name="fuel_prices",
    description="Weekly RON95/RON97/diesel pump prices (data.gov.my)",
    dataset_id=FUELPRICE_DATASET_ID,
    table=TABLE,
    rules=RULES,
    fetch=fetch,
    to_frame=to_frame,
    lookback_days=21,
)
