"""The numbers behind the dashboard. Pure pandas, no Streamlit, so every figure is unit-tested.

Inputs are mart tables as DataFrames (see data.py). Conventions, all from the marts:
- Fuel prices are national. A state's row repeats the national price (DECISIONS.md #33).
- Transport CPI change is in percent; fuel change is in MYR per litre.
- A month whose CPI is not published yet is `awaiting_cpi`. It is left out of every comparison,
  never treated as zero or as a fall.
- The current month's fuel average is `partial`. It is labelled wherever it is shown.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Below half a sen the monthly average did not really move: a price held all month can still
# differ in the fourth decimal from day weighting.
NO_CHANGE_TOLERANCE_MYR = 0.005

RISE, NO_CHANGE, FALL = "rise", "no change", "fall"
DIRECTIONS = [FALL, NO_CHANGE, RISE]

PUBLISHED, AWAITING = "published", "awaiting_cpi"
PARTIAL = "partial"

_FUEL_NAMES = {
    "ron95": "RON95",
    "ron97": "RON97",
    "ron95_budi95": "RON95 (BUDI95)",
    "ron95_skps": "RON95 (SKPS)",
    "diesel": "Diesel",
    "diesel_eastmsia": "Diesel (East Malaysia)",
    "diesel_skds": "Diesel (SKDS)",
}


def fuel_label(fuel_type: str) -> str:
    """Display name for a fuel type. Unknown types still read sensibly."""
    return _FUEL_NAMES.get(fuel_type, fuel_type.replace("_", " ").upper())


def headline_fuel(fvc: pd.DataFrame) -> str:
    """The fuel the central mart describes. Read from the mart, never assumed (DECISIONS.md #41)."""
    fuels = fvc["fuel_type"].dropna().unique()
    if len(fuels) != 1:
        raise ValueError(f"expected one headline fuel in the mart, found {sorted(fuels)}")
    return str(fuels[0])


def direction(change_myr: float) -> str | None:
    """Rise, fall or no change for a monthly fuel change. None when there is no change to judge."""
    if change_myr is None or pd.isna(change_myr):
        return None
    if change_myr >= NO_CHANGE_TOLERANCE_MYR:
        return RISE
    if change_myr <= -NO_CHANGE_TOLERANCE_MYR:
        return FALL
    return NO_CHANGE


def last_months(df: pd.DataFrame, months: int | None, column: str = "month_date") -> pd.DataFrame:
    """Rows from the newest `months` months in `column` (all rows when months is None)."""
    if months is None or df.empty:
        return df
    newest = df[column].max()
    start = (newest.to_period("M") - (months - 1)).to_timestamp()
    return df[df[column] >= start]


def national_monthly(fvc: pd.DataFrame) -> pd.DataFrame:
    """One row per month: the national fuel figures and the average of the states' transport CPI.

    The average is a simple mean of the states, not the official national CPI, which no mart
    holds. The dashboard labels it as such.
    """
    published = fvc[fvc["cpi_status"] == PUBLISHED]
    cpi = published.groupby("month_date").agg(
        transport_cpi_mom_pct=("transport_cpi_mom_pct", "mean"),
        overall_cpi_mom_pct=("overall_cpi_mom_pct", "mean"),
        states_published=("state_code", "nunique"),
    )
    fuel = fvc.groupby("month_date").agg(
        fuel_avg_price_myr=("fuel_avg_price_myr", "first"),
        fuel_mom_change_myr=("fuel_mom_change_myr", "first"),
        fuel_mom_change_prev_month_myr=("fuel_mom_change_prev_month_myr", "first"),
        fuel_month_status=("fuel_month_status", "first"),
        cpi_status=("cpi_status", "first"),
    )
    out = fuel.join(cpi).reset_index().sort_values("month_date")
    out["states_published"] = out["states_published"].fillna(0).astype(int)
    return out


def change_pairs(fvc: pd.DataFrame) -> pd.DataFrame:
    """State-months that can answer the question: a month's fuel change beside the same month's
    transport CPI change. Months awaiting CPI, and the first month (no change yet), drop out.

    Same month, not the month after: CPI prices are collected through the month, so a weekly
    price change shows up in the month it happens (DECISIONS.md #65).
    """
    usable = fvc[
        (fvc["cpi_status"] == PUBLISHED)
        & fvc["fuel_mom_change_myr"].notna()
        & fvc["transport_cpi_mom_pct"].notna()
    ].copy()
    usable["fuel_direction"] = usable["fuel_mom_change_myr"].map(direction)
    return usable


@dataclass(frozen=True)
class PassThrough:
    """How transport CPI moved in months when the fuel price rose, fell or held."""

    by_direction: pd.DataFrame  # direction, mean_transport_mom_pct, months, observations
    gap_pp: float | None  # mean in a rise month minus mean in a no-change month, percentage points
    correlation: float | None  # Pearson r of a month's fuel change and its CPI change
    pp_per_10_sen: float | None  # least-squares slope: CPI change per 10 sen of fuel change
    months: int  # distinct months in the comparison
    # The previous month's slope once the same month's change is in the regression: the check
    # that nothing arrives a month late. None when there is too little variation to fit it.
    next_month_pp_per_10_sen: float | None = None

    def months_when(self, which: str) -> int:
        row = self.by_direction[self.by_direction["direction"] == which]
        return int(row["months"].iloc[0]) if len(row) else 0

    def mean_when(self, which: str) -> float | None:
        row = self.by_direction[self.by_direction["direction"] == which]
        return float(row["mean_transport_mom_pct"].iloc[0]) if len(row) else None


def pass_through(pairs: pd.DataFrame) -> PassThrough:
    """Average transport CPI change in months when fuel rose, fell or held, across the given
    state-months."""
    grouped = (
        pairs.groupby("fuel_direction")
        .agg(
            mean_transport_mom_pct=("transport_cpi_mom_pct", "mean"),
            months=("month_date", "nunique"),
            observations=("transport_cpi_mom_pct", "size"),
        )
        .reindex(DIRECTIONS)
        .dropna(subset=["observations"])
        .rename_axis("direction")
        .reset_index()
    )
    grouped[["months", "observations"]] = grouped[["months", "observations"]].astype(int)

    rise = grouped.loc[grouped["direction"] == RISE, "mean_transport_mom_pct"]
    held = grouped.loc[grouped["direction"] == NO_CHANGE, "mean_transport_mom_pct"]
    gap = float(rise.iloc[0] - held.iloc[0]) if len(rise) and len(held) else None

    x = pairs["fuel_mom_change_myr"].astype(float).to_numpy()
    y = pairs["transport_cpi_mom_pct"].astype(float).to_numpy()
    correlation = slope = None
    if len(x) >= 3 and np.ptp(x) > 0 and np.ptp(y) > 0:
        correlation = float(np.corrcoef(x, y)[0, 1])
        slope = float(np.polyfit(x * 100, y, 1)[0] * 10)  # x in sen, per 10 sen
    return PassThrough(
        grouped,
        gap,
        correlation,
        slope,
        int(pairs["month_date"].nunique()),
        _next_month_slope(pairs),
    )


def _next_month_slope(pairs: pd.DataFrame) -> float | None:
    """CPI change per 10 sen of LAST month's fuel change, holding this month's change fixed."""
    both = pairs.dropna(subset=["fuel_mom_change_prev_month_myr"])
    now = both["fuel_mom_change_myr"].astype(float).to_numpy() * 100
    before = both["fuel_mom_change_prev_month_myr"].astype(float).to_numpy() * 100
    y = both["transport_cpi_mom_pct"].astype(float).to_numpy()
    design = np.column_stack([np.ones(len(y)), now, before])
    if len(y) < 4 or np.linalg.matrix_rank(design) < 3:
        return None
    coefficients = np.linalg.lstsq(design, y, rcond=None)[0]
    return float(coefficients[2] * 10)


def finding(result: PassThrough, fuel: str, scope: str) -> str:
    """The page's one-sentence answer, written from the numbers so it can never overstate them."""
    rises = result.months_when(RISE)
    held = result.mean_when(NO_CHANGE)
    if result.months == 0:
        return "There is no month with both a fuel change and published transport CPI yet."
    if rises == 0:
        return (
            f"{fuel} did not rise in this period, so its effect on {scope} transport CPI cannot "
            "be measured yet."
        )
    month_word = "month" if rises == 1 else "months"
    sentence = (
        f"In the {rises} {month_word} when {fuel} rose, {scope} transport CPI changed by "
        f"{result.mean_when(RISE):+.2f}% on average in the same month"
    )
    if held is not None:
        sentence += f", against {held:+.2f}% in months when the price held"
    return sentence + "."


def latest_published_month(fvc: pd.DataFrame) -> pd.Timestamp | None:
    months = fvc.loc[fvc["cpi_status"] == PUBLISHED, "month_date"]
    return months.max() if len(months) else None


def state_ranking(fvc: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.DataFrame]:
    """States ranked by transport CPI change in the latest month with published CPI.

    Awaiting months are never ranked: a missing figure is not a fall.
    """
    month = latest_published_month(fvc)
    if month is None:
        return None, fvc.iloc[0:0]
    ranked = (
        fvc[(fvc["month_date"] == month) & fvc["transport_cpi_mom_pct"].notna()]
        .sort_values(["transport_cpi_mom_pct", "state_name"], ascending=[False, True])
        .reset_index(drop=True)
    )
    ranked.insert(0, "rank", range(1, len(ranked) + 1))
    return month, ranked


def state_sensitivity(pairs: pd.DataFrame) -> pd.DataFrame:
    """Per state: average transport CPI change in fuel-rise months, in no-change months, and the
    gap.

    The gap is how much more that state's transport CPI rose in months when fuel rose. It is NaN
    for a state with no rise or no unchanged month to compare.
    """
    if pairs.empty:
        return pd.DataFrame(
            columns=["state_name", "region", "with_rise", "with_no_change", "gap_pp", "rises"]
        )
    means = pairs.pivot_table(
        index=["state_name", "region"],
        columns="fuel_direction",
        values="transport_cpi_mom_pct",
        aggfunc="mean",
    ).reindex(columns=DIRECTIONS)
    rises = (
        pairs[pairs["fuel_direction"] == RISE]
        .groupby(["state_name", "region"])["month_date"]
        .nunique()
    )
    out = pd.DataFrame(
        {
            "with_rise": means[RISE],
            "with_no_change": means[NO_CHANGE],
        }
    )
    out["gap_pp"] = out["with_rise"] - out["with_no_change"]
    out["rises"] = rises.reindex(out.index).fillna(0).astype(int)
    return (
        out.reset_index()
        .sort_values(["gap_pp", "state_name"], ascending=[False, True], na_position="last")
        .reset_index(drop=True)
    )


@dataclass(frozen=True)
class PriceNow:
    """A fuel's latest price and how it compares with the price in effect some days earlier."""

    date_now: pd.Timestamp
    price_now: float
    date_then: pd.Timestamp | None
    price_then: float | None
    last_year: list[float]  # weekly prices over the last 52 weeks, oldest first, for a sparkline

    @property
    def change_sen(self) -> float | None:
        if self.price_then is None:
            return None
        return round((self.price_now - self.price_then) * 100, 1)


def price_change_since(prices: pd.DataFrame, fuel_type: str, days: int = 365) -> PriceNow:
    """The latest price against the price in effect `days` earlier (a price holds until the next
    announcement). When the series is shorter than that, compare with its first price."""
    series = prices[prices["fuel_type"] == fuel_type].sort_values("price_date")
    if series.empty:
        raise ValueError(f"no prices for {fuel_type!r}")
    latest = series.iloc[-1]
    target = latest["price_date"] - pd.Timedelta(days=days)
    earlier = series[series["price_date"] <= target]
    then = earlier.iloc[-1] if len(earlier) else (series.iloc[0] if len(series) > 1 else None)
    return PriceNow(
        date_now=latest["price_date"],
        price_now=float(latest["price_myr"]),
        date_then=None if then is None else then["price_date"],
        price_then=None if then is None else float(then["price_myr"]),
        last_year=series["price_myr"].astype(float).tail(52).tolist(),
    )


def price_changes(prices: pd.DataFrame, fuel_type: str) -> pd.DataFrame:
    """Every announcement that changed a fuel's price, newest first, with any reviewed event."""
    changes = prices[(prices["fuel_type"] == fuel_type) & prices["is_price_change"].fillna(False)]
    return changes.sort_values("price_date", ascending=False)[
        ["price_date", "previous_price_myr", "price_myr", "change_myr", "event_description"]
    ].reset_index(drop=True)


def division_yoy(cpi: pd.DataFrame, divisions: pd.DataFrame, state_code: str) -> pd.DataFrame:
    """Year-on-year CPI change by division for one state, in its latest month with that figure."""
    state = cpi[(cpi["state_code"] == state_code) & cpi["yoy_pct"].notna()]
    if state.empty:
        return pd.DataFrame(columns=["month_date", "division_name", "yoy_pct", "is_all_items"])
    latest = state[state["month_date"] == state["month_date"].max()]
    return (
        latest.merge(divisions, on="division_code")[
            ["month_date", "division_code", "division_name", "yoy_pct", "is_all_items"]
        ]
        .sort_values("yoy_pct", ascending=False)
        .reset_index(drop=True)
    )


def synthetic_sources(health: pd.DataFrame) -> list[str]:
    """Labels of the sources that hold any synthetic rows. Empty means all data is real."""
    return health.loc[health["has_synthetic_rows"].astype(bool), "source_label"].tolist()
