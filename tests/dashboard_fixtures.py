"""Small mart tables, shaped like the real marts, with numbers chosen to be checked by hand.

Three states, January to June 2026. RON95 rises 5 sen in March (felt in April's CPI) and falls
10 sen in May (felt in June, whose CPI is not published yet). June is the partial fuel month.

Transport CPI change, % on month:
             Feb   Mar   Apr   May   Jun
  Selangor   0.1   0.2   0.6   0.0   awaiting
  Johor      0.3   0.1   0.4  -0.2   awaiting
  Sabah      0.2   0.3   0.2   0.1   awaiting

Comparable state-months (previous fuel change known, CPI published): March and May follow a month
with no change, April follows the rise. So:
  after a rise      = mean(0.6, 0.4, 0.2)                  = 0.4     (1 month)
  after no change   = mean(0.2, 0.1, 0.3, 0.0, -0.2, 0.1)  = 0.08333 (2 months)
  gap               = 0.31667 pp
"""

from __future__ import annotations

import numpy as np
import pandas as pd

NAN = np.nan
MONTHS = pd.to_datetime(
    ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"]
)
FUEL_AVG = [2.05, 2.05, 2.10, 2.10, 2.00, 2.00]
FUEL_MOM = [NAN, 0.0, 0.05, 0.0, -0.10, 0.0]
FUEL_PREV = [NAN, NAN, 0.0, 0.05, 0.0, -0.10]
STATES = [
    ("MY-10", "Selangor", "Peninsular", [NAN, 0.1, 0.2, 0.6, 0.0, NAN]),
    ("MY-01", "Johor", "Peninsular", [NAN, 0.3, 0.1, 0.4, -0.2, NAN]),
    ("MY-12", "Sabah", "East Malaysia", [NAN, 0.2, 0.3, 0.2, 0.1, NAN]),
]


def fuel_vs_cpi() -> pd.DataFrame:
    rows = []
    for code, name, region, transport in STATES:
        index = 110.0
        for i, month in enumerate(MONTHS):
            awaiting = i == len(MONTHS) - 1
            if not np.isnan(transport[i]):
                index *= 1 + transport[i] / 100
            rows.append(
                {
                    "state_month_key": f"{code}-{month:%Y%m}",
                    "month_date": month,
                    "state_code": code,
                    "state_name": name,
                    "region": region,
                    "fuel_type": "ron95",
                    "fuel_avg_price_myr": FUEL_AVG[i],
                    "fuel_days_priced": 30,
                    "fuel_announcements": 4,
                    "fuel_mom_change_myr": FUEL_MOM[i],
                    "fuel_mom_change_prev_month_myr": FUEL_PREV[i],
                    "transport_cpi": NAN if awaiting else round(index, 1),
                    "transport_cpi_mom_pct": transport[i],
                    "overall_cpi": NAN if awaiting else 130.0,
                    "overall_cpi_mom_pct": NAN if awaiting or i == 0 else 0.2,
                    "fuel_month_status": "partial" if awaiting else "complete",
                    "cpi_status": "awaiting_cpi" if awaiting else "published",
                    "built_at": pd.Timestamp("2026-06-20T08:00:00Z"),
                }
            )
    return pd.DataFrame(rows)


def fuel_prices() -> pd.DataFrame:
    def row(day, fuel, price, previous, event=None):
        change = NAN if previous is None else round(price - previous, 2)
        return {
            "fuel_price_key": f"{fuel}-{day}",
            "price_date": pd.Timestamp(day),
            "fuel_type": fuel,
            "price_myr": price,
            "previous_price_myr": NAN if previous is None else previous,
            "change_myr": change,
            "days_since_previous": 7,
            "is_price_change": previous is not None and price != previous,
            "event_description": event,
        }

    return pd.DataFrame(
        [
            row("2025-06-01", "ron95", 2.05, None),
            row("2026-02-26", "ron95", 2.05, 2.05),
            row("2026-03-05", "ron95", 2.10, 2.05, "Subsidy rationalisation, phase one"),
            row("2026-05-07", "ron95", 2.00, 2.10),
            row("2026-06-04", "ron95", 2.00, 2.00),
            row("2025-06-01", "ron97", 3.47, None),
            row("2026-03-05", "ron97", 3.60, 3.47),
            row("2025-06-01", "diesel", 2.15, None),
            row("2026-06-04", "diesel", 2.15, 2.15),
        ]
    )


def cpi_state() -> pd.DataFrame:
    rows = []
    yoy = {"overall": 2.0, "07": 3.5, "01": 1.5, "04": 0.8}
    for month, factor in [("2026-04-01", None), ("2026-05-01", 1.0)]:
        for division, value in yoy.items():
            rows.append(
                {
                    "cpi_key": f"MY-10-{month}-{division}",
                    "state_code": "MY-10",
                    "state_name": "Selangor",
                    "month_date": pd.Timestamp(month),
                    "division_code": division,
                    "cpi_index": 120.0,
                    "mom_pct": 0.1,
                    "yoy_pct": NAN if factor is None else value,
                }
            )
    return pd.DataFrame(rows)


def divisions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "division_code": ["01", "04", "07", "overall"],
            "division_name": [
                "Food and non-alcoholic beverages",
                "Housing",
                "Transport",
                "All items",
            ],
            "is_all_items": [False, False, False, True],
        }
    )


def states() -> pd.DataFrame:
    return pd.DataFrame(
        [(code, name, region) for code, name, region, _ in STATES],
        columns=["state_code", "state_name", "region"],
    )


def fuel_types() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "fuel_type": ["ron95", "ron97", "diesel"],
            "fuel_family": ["petrol", "petrol", "diesel"],
            "is_headline": [True, False, False],
            "first_price_date": pd.to_datetime(["2025-06-01"] * 3),
            "last_price_date": pd.to_datetime(["2026-06-04"] * 3),
            "weeks_published": [54, 54, 54],
        }
    )


def data_health(synthetic: bool = True) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source_name": ["cpi_state", "fuel_prices", "fx_rates"],
            "source_label": [
                "State CPI (OpenDOSM)",
                "Fuel prices (data.gov.my)",
                "Exchange rates (BNM)",
            ],
            "row_count": [24, 9, 0],
            "synthetic_row_count": [24, 9, 0] if synthetic else [0, 0, 0],
            "has_synthetic_rows": [True, True, False] if synthetic else [False, False, False],
            "earliest_data_date": pd.to_datetime(["2026-01-01", "2025-06-01", None]),
            "latest_data_date": pd.to_datetime(["2026-05-01", "2026-06-04", None]),
            "last_run_at": pd.to_datetime([None, "2026-06-05T01:00:00Z", None], utc=True),
            "last_run_status": [None, "success", None],
            "last_success_at": pd.to_datetime([None, "2026-06-05T01:01:00Z", None], utc=True),
            "failed_runs": [0, 0, 0],
            "built_at": pd.to_datetime(["2026-06-20T08:00:00Z"] * 3, utc=True),
        }
    )


def marts(synthetic: bool = True) -> dict[str, pd.DataFrame]:
    """Every mart the dashboard reads, by name."""
    return {
        "fct_fuel_vs_transport_cpi": fuel_vs_cpi(),
        "fct_fuel_prices": fuel_prices(),
        "fct_cpi_state": cpi_state(),
        "dim_division": divisions(),
        "dim_state": states(),
        "dim_fuel_type": fuel_types(),
        "mart_data_health": data_health(synthetic),
    }
