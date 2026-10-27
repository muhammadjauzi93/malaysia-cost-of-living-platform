"""Daily MYR exchange rates from Bank Negara Malaysia (`/exchange-rate`).

The daily run asks for the *latest* published table, which needs no date handling at all:
on a weekend or holiday BNM simply returns the last published day, and the upsert makes that a
no-op. Historical gaps are filled on demand with `--since`, which calls the per-date endpoint
and treats a 404 as "BNM published nothing that day". See DECISIONS.md #16 and #17.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pandas as pd

from col_pipeline.config import Settings
from col_pipeline.errors import DataContractError
from col_pipeline.extract import BnmClient
from col_pipeline.sources.base import QualityRules, Source, TableSpec

log = logging.getLogger(__name__)

# Scoped to the project's question: oil is priced in USD; SGD and CNY are key trade partners;
# EUR and JPY exercise the quoting-unit logic (JPY is quoted per 100).
TRACKED_CURRENCIES = ("USD", "SGD", "CNY", "EUR", "JPY")
OUTPUT_COLUMNS = [
    "rate_date",
    "currency",
    "session_code",
    "unit",
    "buying_rate",
    "selling_rate",
    "middle_rate",
]
RATE_COLUMNS = ["buying_rate", "selling_rate", "middle_rate"]

TABLE = TableSpec(
    schema="raw",
    table="fx_rate_daily",
    key_columns=("rate_date", "currency", "session_code"),
    value_columns=("unit", "buying_rate", "selling_rate", "middle_rate"),
    date_column="rate_date",
)

# - min_rows=3: tolerate one or two missing currencies (reported as a WARN), not a broken table.
# - max_age_days=5: a long weekend plus a public holiday can leave four days without a rate.
# - Rates are stored as BNM quotes them (JPY per 100), so bounds are wide.
RULES = QualityRules(
    min_rows=3,
    max_age_days=5,
    value_ranges={col: (0.01, 1000.0) for col in RATE_COLUMNS},
    nullable_columns=tuple(RATE_COLUMNS),
    ordered_pairs=(("buying_rate", "selling_rate"),),
    required_values={"currency": TRACKED_CURRENCIES},
)


def _item_date(item: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(item["rate"]["date"])
    except (KeyError, TypeError, ValueError):
        return None


def weekdays_between(start: date, end_exclusive: date) -> list[date]:
    """Mon-Fri dates in [start, end_exclusive). BNM does not publish on weekends."""
    days = (end_exclusive - start).days
    return [d for d in (start + timedelta(n) for n in range(max(days, 0))) if d.weekday() < 5]


def fetch_history(
    client: BnmClient,
    start: date,
    end_exclusive: date,
    session_code: str,
    *,
    pause_s: float = 0.2,
    sleep: Callable[[float], None] | None = None,
) -> list[dict[str, Any]]:
    """Per-date, per-currency lookups. Slower and less proven than `latest_rates`, so opt-in."""
    pause = sleep if sleep is not None else time.sleep  # resolved at call time: patchable
    records: list[dict[str, Any]] = []
    requested = 0
    for day in weekdays_between(start, end_exclusive):
        for currency in TRACKED_CURRENCIES:
            requested += 1
            for item in client.rates_on(currency, day, session_code):
                records.append({**item, "_session": session_code})
            pause(pause_s)
    log.info("History: %d lookups, %d rates found", requested, len(records))
    if requested and not records:
        log.warning(
            "History returned nothing for %d lookups. Either BNM published nothing in that "
            "range or the per-date endpoint has changed: run `col-pipeline probe-bnm`.",
            requested,
        )
    return records


def fetch(settings: Settings, since: date | None) -> list[dict[str, Any]]:
    client = BnmClient(settings.bnm_base_url, settings.request_timeout_s)
    session_code = settings.bnm_session
    log.info("Requesting latest BNM rates (session %s)", session_code)
    items = client.latest_rates(session_code)
    records = [{**item, "_session": session_code} for item in items]
    log.info("Received %d currencies", len(records))

    if since is not None:
        dates = [d for d in (_item_date(i) for i in items) if d is not None]
        latest = max(dates) if dates else None
        if latest is not None:
            records += fetch_history(client, since, latest, session_code)
    return records


def to_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten BNM's {currency_code, unit, rate: {...}} items into one row per currency."""
    if not records:
        raise DataContractError("Extract returned zero records")

    rows = []
    for rec in records:
        try:
            rate = rec["rate"]
            rows.append(
                {
                    "rate_date": rate["date"],
                    "currency": rec["currency_code"],
                    "session_code": rec["_session"],
                    "unit": rec["unit"],
                    "buying_rate": rate.get("buying_rate"),
                    "selling_rate": rate.get("selling_rate"),
                    "middle_rate": rate.get("middle_rate"),
                }
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise DataContractError(
                f"Unexpected BNM record shape ({exc!r}): {str(rec)[:200]}. "
                "Run `col-pipeline probe-bnm` to see what the API returns now."
            ) from exc

    df = pd.DataFrame(rows)
    df["currency"] = df["currency"].astype(str).str.strip().str.upper()
    df = df[df["currency"].isin(TRACKED_CURRENCIES)].copy()
    if df.empty:
        raise DataContractError(f"None of the tracked currencies {TRACKED_CURRENCIES} were present")

    df["rate_date"] = pd.to_datetime(df["rate_date"], errors="coerce").dt.date
    df["unit"] = pd.to_numeric(df["unit"], errors="coerce")
    for col in RATE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").round(4)

    # A row with no rate at all carries no information. A row with some rates is kept.
    df = df.dropna(subset=["rate_date", "unit"])
    df = df.dropna(subset=RATE_COLUMNS, how="all")
    df["unit"] = df["unit"].astype(int)

    before = len(df)
    df = df.drop_duplicates(subset=["rate_date", "currency", "session_code"], keep="last")
    if dropped := before - len(df):
        log.info("Collapsed %d repeated (rate_date, currency, session_code) rows", dropped)

    df = df.sort_values(["rate_date", "currency"]).reset_index(drop=True)
    if df.empty:
        raise DataContractError("Transform produced zero rows")

    log.info(
        "Transformed %d FX rows for %s, dates %s to %s",
        len(df),
        ", ".join(sorted(df["currency"].unique())),
        df["rate_date"].min(),
        df["rate_date"].max(),
    )
    return df[OUTPUT_COLUMNS]


SOURCE = Source(
    name="fx_rates",
    description="Daily MYR exchange rates for USD, SGD, CNY, EUR, JPY (Bank Negara Malaysia)",
    dataset_id="bnm_exchange_rate",
    table=TABLE,
    rules=RULES,
    fetch=fetch,
    to_frame=to_frame,
    lookback_days=0,
    incremental_from_watermark=False,
)
