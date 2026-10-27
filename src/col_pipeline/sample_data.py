"""Synthetic sample data for CI, demos and dbt development. NOT real prices, indices or rates.

Why this exists: the dbt layer (Phase 3) needs realistic *volumes and shapes* to be tested:
sixteen states, fourteen CPI divisions, several fuel types, five currencies. The unit-test
fixtures hold two states and a handful of rows, which cannot exercise tests such as "every
month has 16 states". Real data cannot be used in CI because CI has no network access to the
sources and must be repeatable.

Three safeguards keep the fake numbers from being mistaken for real ones:

1. Everything is deterministic. The same `end` date always produces the same frames, so a
   failing test can be reproduced exactly.
2. Every loaded row is labelled `source = 'synthetic-sample'`, which flows through dbt, so a
   mart can be checked for contamination.
3. `load_sample` refuses to touch a database that already has data in its raw tables.

The frames have exactly the shape each source's `to_frame` returns, and they are loaded with
the real `PostgresLoader`, so the sample exercises the same code path as real data.
"""

from __future__ import annotations

import logging
import random
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import Engine, text

from col_pipeline.load import PostgresLoader
from col_pipeline.quality import blocking_failures, evaluate_frame, format_failures
from col_pipeline.sources import SOURCES

log = logging.getLogger(__name__)

SAMPLE_SOURCE_LABEL = "synthetic-sample"
SEED = 20261027  # fixed: changing it changes every sample value

# The sixteen states and federal territories as OpenDOSM names them in `cpi_state`.
STATES = (
    "Johor",
    "Kedah",
    "Kelantan",
    "Melaka",
    "Negeri Sembilan",
    "Pahang",
    "Perak",
    "Perlis",
    "Pulau Pinang",
    "Sabah",
    "Sarawak",
    "Selangor",
    "Terengganu",
    "W.P. Kuala Lumpur",
    "W.P. Labuan",
    "W.P. Putrajaya",
)
DIVISIONS = tuple(f"{n:02d}" for n in range(1, 14)) + ("overall",)

# (base ringgit per `unit`, unit). JPY is quoted per 100 units, like BNM does.
FX_BASES = {
    "USD": (4.40, 1),
    "SGD": (3.30, 1),
    "CNY": (0.62, 1),
    "EUR": (4.80, 1),
    "JPY": (3.00, 100),
}


def last_thursday_on_or_before(day: date) -> date:
    return day - timedelta(days=(day.weekday() - 3) % 7)


def last_weekday_on_or_before(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def first_of_month(day: date) -> date:
    return day.replace(day=1)


def months_back(start: date, n: int) -> date:
    """The 1st of the month `n` months before `start`'s month."""
    index = start.year * 12 + (start.month - 1) - n
    return date(index // 12, index % 12 + 1, 1)


def build_fuel_prices(end: date, weeks: int = 104) -> pd.DataFrame:
    """Weekly prices, one row per (price_date, fuel_type), ending on the last Thursday.

    Prices change rarely (about one week in six), like the real series, so the sample also
    exercises the "price unchanged for many weeks" case that broke freshness-on-loaded_at.
    A subsidised-RON95 column only exists for the last 30 weeks, like a newly added fuel type.
    """
    rng = random.Random(f"{SEED}-fuel")
    last = last_thursday_on_or_before(end)
    thursdays = [last - timedelta(weeks=w) for w in range(weeks - 1, -1, -1)]

    ron95, ron97, diesel = 2.05, 3.47, 2.95
    rows: list[tuple[date, str, float]] = []
    for i, day in enumerate(thursdays):
        if rng.random() < 0.17:
            ron95 = min(3.20, max(1.90, round(ron95 + rng.choice((-0.10, -0.05, 0.05, 0.10)), 2)))
            ron97 = min(4.80, max(2.80, round(ron97 + rng.choice((-0.15, -0.05, 0.05, 0.15)), 2)))
            diesel = min(4.20, max(2.10, round(diesel + rng.choice((-0.20, -0.10, 0.10, 0.20)), 2)))
        rows += [(day, "ron95", ron95), (day, "ron97", ron97), (day, "diesel", diesel)]
        if i >= weeks - 30:
            rows.append((day, "ron95_budi95", 1.99))

    out = pd.DataFrame(rows, columns=["price_date", "fuel_type", "price_myr"])
    return out.sort_values(["price_date", "fuel_type"]).reset_index(drop=True)


def build_cpi_state(end: date, months: int = 36) -> pd.DataFrame:
    """Monthly CPI for every state and division. Newest month is the previous calendar month.

    A month is published around the 19th of the next month, so the previous month is the
    newest that can exist, and it is always inside the 90-day freshness limit.
    """
    rng = random.Random(f"{SEED}-cpi")
    newest = months_back(first_of_month(end), 1)
    month_dates = [months_back(newest, m) for m in range(months - 1, -1, -1)]

    rows: list[tuple[str, date, str, float]] = []
    for state in STATES:
        state_level = rng.uniform(96.0, 112.0)
        index = {d: state_level * rng.uniform(0.94, 1.12) for d in DIVISIONS if d != "overall"}
        for month in month_dates:
            for division in index:
                index[division] *= 1 + rng.uniform(-0.002, 0.006)  # slow upward drift
                rows.append((state, month, division, round(index[division], 3)))
            overall = sum(index.values()) / len(index)
            rows.append((state, month, "overall", round(overall, 3)))

    out = pd.DataFrame(rows, columns=["state", "month_date", "division", "cpi_index"])
    return out.sort_values(["month_date", "state", "division"]).reset_index(drop=True)


def build_fx_rates(end: date, days: int = 180, session_code: str = "1700") -> pd.DataFrame:
    """Weekday rates for the five tracked currencies, ending on the last weekday."""
    rng = random.Random(f"{SEED}-fx")
    last = last_weekday_on_or_before(end)
    weekdays = [
        d for d in (last - timedelta(n) for n in range(days - 1, -1, -1)) if d.weekday() < 5
    ]

    level = {ccy: base for ccy, (base, _unit) in FX_BASES.items()}
    rows: list[tuple[date, str, str, int, float, float, float]] = []
    for day in weekdays:
        for ccy, (_base, unit) in FX_BASES.items():
            level[ccy] *= 1 + rng.uniform(-0.004, 0.004)
            middle = level[ccy]
            rows.append(
                (
                    day,
                    ccy,
                    session_code,
                    unit,
                    round(middle * 0.999, 4),
                    round(middle * 1.001, 4),
                    round(middle, 4),
                )
            )
    cols = [
        "rate_date",
        "currency",
        "session_code",
        "unit",
        "buying_rate",
        "selling_rate",
        "middle_rate",
    ]
    return pd.DataFrame(rows, columns=cols)


BUILDERS = {
    "fuel_prices": build_fuel_prices,
    "cpi_state": build_cpi_state,
    "fx_rates": build_fx_rates,
}


class SampleDataError(RuntimeError):
    """The sample cannot be loaded safely (database not empty, or the sample fails the gate)."""


def load_sample(engine: Engine, end: date) -> dict[str, int]:
    """Load the synthetic sample into an EMPTY database. Returns rows loaded per source.

    Refuses if any raw table already holds rows: this is a safety rail, because mixing fake
    and real data is the one mistake that cannot be undone by re-running anything.
    """
    with engine.connect() as conn:
        occupied = {
            s.table.qualified: n
            for s in SOURCES.values()
            if (n := conn.execute(text(f"SELECT count(*) FROM {s.table.qualified}")).scalar_one())
        }
    if occupied:
        listing = ", ".join(f"{t} ({n} rows)" for t, n in occupied.items())
        raise SampleDataError(
            f"Refusing to load sample data: these tables already contain data: {listing}. "
            "Use a scratch database (`python tasks.py reset-db`) or CI."
        )

    frames = {name: BUILDERS[name](end) for name in SOURCES}

    # The sample has to pass the same gate real data passes; if it does not, the generator is
    # wrong, and loading it would hide that.
    for name, frame in frames.items():
        source = SOURCES[name]
        if failures := blocking_failures(evaluate_frame(frame, source.table, source.rules)):
            raise SampleDataError(
                f"Sample for {name} fails the quality gate: {format_failures(failures)}"
            )

    loader = PostgresLoader(engine)
    loaded: dict[str, int] = {}
    for name, frame in frames.items():
        table = SOURCES[name].table
        loader.upsert(table, frame)
        with engine.begin() as conn:
            conn.execute(
                text(f"UPDATE {table.qualified} SET source = :label"),
                {"label": SAMPLE_SOURCE_LABEL},
            )
        loaded[name] = len(frame)
    log.info("Loaded synthetic sample data: %s", loaded)
    return loaded
