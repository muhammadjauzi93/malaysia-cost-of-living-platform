"""Transform: raw API records -> a typed, validated, long-format DataFrame.

Pure functions only: no network, no database, no clock. Same input -> same output.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

ID_COLUMNS = {"date", "series_type"}  # everything else the API sends is a fuel price
OUTPUT_COLUMNS = ["price_date", "fuel_type", "price_myr"]

# Plausible MYR-per-litre bounds. A value outside this means something broke upstream.
MIN_PRICE_MYR = 0.50
MAX_PRICE_MYR = 10.00


class DataContractError(ValueError):
    """The data broke an assumption that downstream code relies on."""


def to_long_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
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
    _check_contract(long)
    log.info(
        "Transformed %d rows across fuel types: %s",
        len(long),
        ", ".join(sorted(long["fuel_type"].unique())),
    )
    return long[OUTPUT_COLUMNS]


def _check_contract(df: pd.DataFrame) -> None:
    """Fail loudly, fail early. Loading garbage quietly is worse than not loading."""
    if df.empty:
        raise DataContractError("Transform produced zero rows")

    bad = df[~df["price_myr"].between(MIN_PRICE_MYR, MAX_PRICE_MYR)]
    if not bad.empty:
        sample = bad[OUTPUT_COLUMNS].head(3).to_dict("records")
        raise DataContractError(
            f"{len(bad)} prices outside [{MIN_PRICE_MYR}, {MAX_PRICE_MYR}] MYR, e.g. {sample}"
        )

    if df.duplicated(subset=["price_date", "fuel_type"]).any():
        raise DataContractError("Primary key (price_date, fuel_type) is not unique")
