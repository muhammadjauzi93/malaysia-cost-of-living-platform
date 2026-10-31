"""Every number and chart in docs/writeup/README.md, recomputed from the marts.

Run after `python tasks.py dbt-build`:

    uv run --with matplotlib python docs/writeup/analysis.py

matplotlib is not a project dependency; `--with` adds it for this run only. Reads the marts with
the pipeline's own connection settings (.env), prints the figures, and rewrites the SVGs here.
"""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from dotenv import dotenv_values
from sqlalchemy import create_engine

from col_pipeline.config import db_url_from_env
from col_pipeline.dashboard.analysis import direction, national_monthly
from col_pipeline.dashboard.data import tidy

OUT = Path(__file__).parent

# Pricing regimes of RON95, read off the fuel series itself.
FLOAT = ("2017-04-01", "2021-03-01")  # weekly managed float
CAP = ("2021-04-01", "2025-09-01")  # held at RM2.05
BUDI = ("2025-11-01", "2026-09-01")  # BUDI95: eligible RM1.99, `ron95` is the unsubsidised price
SWITCH = "2025-10-01"  # the month BUDI95 started, left out of the BUDI95 slope (a change of regime)
SHOCK_FROM, SHOCK_TO = "2026-02-01", "2026-09-01"

# Reference palette (dataviz skill, light mode).
BLUE, ORANGE, MUTED_INK = "#2a78d6", "#eb6834", "#898781"
INK, INK2, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7", "#fcfcfb"

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK2,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.color": MUTED_INK,
        "ytick.color": MUTED_INK,
        "font.size": 10,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
        "axes.titlelocation": "left",
        "legend.frameon": False,
        "svg.hashsalt": "writeup",  # stable SVG ids, so a re-run only diffs when the data does
    }
)


def read(name: str) -> pd.DataFrame:
    env = {**dotenv_values(".env"), **os.environ}
    engine = create_engine(db_url_from_env(env))
    return tidy(pd.read_sql(f"select * from marts.{name}", engine))


def between(df: pd.DataFrame, span: tuple[str, str], column: str = "month_date") -> pd.DataFrame:
    return df[df[column].between(*span)]


def fit(x: pd.Series, y: pd.Series) -> tuple[float, float, float]:
    """Slope, intercept and R² of a least-squares line."""
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept), float(np.corrcoef(x, y)[0, 1] ** 2)


def ols(df: pd.DataFrame, xs: list[str], y: str) -> tuple[np.ndarray, float]:
    d = df.dropna(subset=[*xs, y])
    X = np.column_stack([np.ones(len(d))] + [d[x] for x in xs])
    b = np.linalg.lstsq(X, d[y], rcond=None)[0]
    resid = d[y] - X @ b
    return b, float(1 - (resid**2).sum() / ((d[y] - d[y].mean()) ** 2).sum())


def main() -> None:
    fvc = read("fct_fuel_vs_transport_cpi").sort_values(["state_name", "month_date"])
    prices = read("fct_fuel_prices")
    cpi = read("fct_cpi_state").merge(read("dim_division"), on="division_code")
    health = read("mart_data_health")

    print("== Data")
    print(
        health[
            [
                "source_label",
                "row_count",
                "synthetic_row_count",
                "earliest_data_date",
                "latest_data_date",
            ]
        ].to_string(index=False)
    )

    nat = national_monthly(fvc)
    nat["fuel_pct"] = nat["fuel_avg_price_myr"].pct_change() * 100
    nat["fuel_pct_prev"] = nat["fuel_pct"].shift(1)
    nat["transport_index"] = nat["transport_cpi_mom_pct"].fillna(0).div(100).add(1).cumprod() * 100

    print("\n== 1. Pass-through is immediate (national, simple mean of 16 states)")
    for label, span in (("float", FLOAT), ("cap", CAP), ("BUDI95", BUDI)):
        d = between(nat, span)
        same, r2_same = ols(d, ["fuel_pct"], "transport_cpi_mom_pct")
        lag, r2_lag = ols(d, ["fuel_pct_prev"], "transport_cpi_mom_pct")
        both, r2_both = ols(d, ["fuel_pct", "fuel_pct_prev"], "transport_cpi_mom_pct")
        print(
            f"{label:7} n={len(d):3}  same month {same[1]:.3f} (R² {r2_same:.2f})  "
            f"next month alone {lag[1]:.3f} (R² {r2_lag:.2f})  "
            f"both: same {both[1]:.3f}, next {both[2]:.3f} (R² {r2_both:.2f})"
        )
    d = between(nat, CAP)
    print(
        f"cap era transport CPI: mean {d.transport_cpi_mom_pct.mean():+.2f}% a month, "
        f"sd {d.transport_cpi_mom_pct.std():.2f}"
    )
    d = between(nat, FLOAT)
    print(f"autocorrelation of monthly fuel change, float era: {d.fuel_pct.autocorr():.2f}")

    lagged = nat.assign(
        before=nat["fuel_mom_change_myr"].shift(1).map(direction),
        now=nat["fuel_mom_change_myr"].map(direction),
    ).dropna(subset=["before", "now", "transport_cpi_mom_pct"])
    print("transport CPI by last month's and this month's fuel direction:")
    print(lagged.groupby(["before", "now"]).transport_cpi_mom_pct.agg(["mean", "count"]).round(2))

    print("\n== 2. BUDI95")
    sw = nat.set_index("month_date").loc[SWITCH]
    print(
        f"Oct 2025: headline RON95 {sw.fuel_pct:+.1f}%, "
        f"transport CPI {sw.transport_cpi_mom_pct:+.2f}%"
    )
    p = nat.set_index("month_date")["fuel_avg_price_myr"]
    t = nat.set_index("month_date")["transport_index"]
    o = fvc.groupby("month_date")["overall_cpi"].mean()
    fuel_rise = p[SHOCK_TO] / p[SHOCK_FROM] - 1
    tr_rise = t[SHOCK_TO] / t[SHOCK_FROM] - 1
    float_slope = ols(between(nat, FLOAT), ["fuel_pct"], "transport_cpi_mom_pct")[0][1]
    print(
        f"Feb->Sep 2026: unsubsidised RON95 {fuel_rise:+.1%}, transport CPI {tr_rise:+.1%}, "
        f"overall CPI {o[SHOCK_TO] / o[SHOCK_FROM] - 1:+.1%}"
    )
    print(
        f"  at the float-era slope transport CPI would have risen about "
        f"{float_slope * fuel_rise:.0%} (linear, rough)"
    )
    budi = prices[(prices.fuel_type == "ron95_budi95")]
    print(f"  ron95_budi95 prices seen: {sorted(budi.price_myr.unique())}")
    peak = (
        prices[prices.price_date.between("2026-01-01", "2026-10-31")]
        .groupby("fuel_type")
        .price_myr.max()
    )
    print(f"  2026 peaks: {peak.round(2).to_dict()}")

    print("\n== 3. States")
    fvc["fuel_pct"] = fvc.groupby("state_name")["fuel_avg_price_myr"].pct_change() * 100
    float_rows = between(fvc, FLOAT).dropna(subset=["fuel_pct", "transport_cpi_mom_pct"])
    level = lambda m, c: fvc[fvc.month_date == m].set_index("state_name")[c]  # noqa: E731
    states = pd.DataFrame(
        {
            "region": level(SHOCK_FROM, "region"),
            "float_slope": float_rows.groupby("state_name").apply(
                lambda d: fit(d.fuel_pct, d.transport_cpi_mom_pct)[0], include_groups=False
            ),
            "shock_rise_pct": (
                level(SHOCK_TO, "transport_cpi") / level(SHOCK_FROM, "transport_cpi") - 1
            )
            * 100,
            "switch_pct": fvc[fvc.month_date == SWITCH].set_index("state_name")[
                "transport_cpi_mom_pct"
            ],
        }
    ).sort_values("float_slope", ascending=False)
    print(states.round(2).to_string())
    r = np.corrcoef(states.float_slope, states.shock_rise_pct)[0, 1]
    rho = states[["float_slope", "shock_rise_pct"]].rank().corr().iloc[0, 1]
    print(f"float slope vs 2026 rise: Pearson {r:.2f}, Spearman {rho:.2f}")

    print("\n== 4. Transport among the divisions (simple mean of states, YoY %)")
    for month in ("2025-09-01", "2026-09-01"):
        rows = cpi[(cpi.month_date == month) & ~cpi.is_all_items.astype(bool)]
        yoy = rows.groupby("division_name").yoy_pct.mean().sort_values(ascending=False)
        print(
            month,
            "rank of Transport:",
            list(yoy.index).index("Transport") + 1,
            "of",
            len(yoy),
            "| all items",
            round(cpi[(cpi.month_date == month) & cpi.is_all_items.astype(bool)].yoy_pct.mean(), 2),
        )
        print(yoy.round(2).to_string())

    chart_prices(prices, nat)
    chart_pass_through(nat)
    chart_states(states)
    print(f"\nCharts written to {OUT}")


def chart_prices(prices: pd.DataFrame, nat: pd.DataFrame) -> None:
    weekly = prices[prices.fuel_type.isin(["ron95", "ron95_budi95"])].pivot_table(
        index="price_date", columns="fuel_type", values="price_myr"
    )
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 6.2), sharex=True, height_ratios=[1.1, 1])
    top.step(weekly.index, weekly["ron95"], where="post", color=BLUE, lw=2)
    top.step(weekly.index, weekly["ron95_budi95"], where="post", color=ORANGE, lw=2)
    top.text(
        pd.Timestamp("2026-10-29"),
        4.42,
        "  Unsubsidised RON95",
        color=INK2,
        va="center",
        fontsize=9,
    )
    top.text(
        pd.Timestamp("2026-10-29"),
        1.99,
        "  BUDI95 (eligible\n  citizens)",
        color=INK2,
        va="center",
        fontsize=9,
    )
    top.set_title("RON95 pump price, RM per litre")
    top.set_ylim(1, 5)
    for x, label in (
        ("2017-04-01", "Weekly float"),
        ("2021-04-01", "Held at RM2.05"),
        ("2025-10-01", "BUDI95"),
    ):
        top.axvline(pd.Timestamp(x), color=AXIS, lw=0.8, ls="--")
        top.text(pd.Timestamp(x), 4.75, f" {label}", color=INK2, fontsize=9)
    m = nat.dropna(subset=["transport_cpi_mom_pct"])
    bottom.plot(m.month_date, m.transport_index, color=INK, lw=2)
    bottom.set_title("Transport CPI, mean of 16 states (April 2017 = 100)")
    fig.tight_layout()
    fig.subplots_adjust(right=0.84)
    fig.savefig(OUT / "ron95_and_transport_cpi.svg")
    plt.close(fig)


def chart_pass_through(nat: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 5))
    for span, color, label in (
        (FLOAT, BLUE, "Weekly float, 2017-21"),
        (BUDI, ORANGE, "BUDI95, Nov 2025-Sep 2026"),
    ):
        d = between(nat, span).dropna(subset=["fuel_pct", "transport_cpi_mom_pct"])
        slope, intercept, r2 = fit(d.fuel_pct, d.transport_cpi_mom_pct)
        ax.scatter(
            d.fuel_pct,
            d.transport_cpi_mom_pct,
            s=34,
            color=color,
            edgecolor=SURFACE,
            lw=1.2,
            zorder=3,
            label=f"{label}: slope {slope:.2f}, R² {r2:.2f}",
        )
        xs = np.linspace(d.fuel_pct.min(), d.fuel_pct.max(), 2)
        ax.plot(xs, intercept + slope * xs, color=color, lw=2)
    ax.axhline(0, color=AXIS, lw=0.8)
    ax.axvline(0, color=AXIS, lw=0.8)
    ax.set_xlabel("RON95 price change that month (%)")
    ax.set_ylabel("Transport CPI change, same month (%)")
    ax.set_title("A 10% pump price move used to move transport CPI 5%. Now under 1%.")
    ax.legend(loc="upper left", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "pass_through_by_regime.svg")
    plt.close(fig)


def chart_states(states: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(8, 5.6))
    for region, color in (("Peninsular", BLUE), ("East Malaysia", ORANGE)):
        d = states[states.region == region]
        ax.scatter(
            d.float_slope,
            d.shock_rise_pct,
            s=44,
            color=color,
            edgecolor=SURFACE,
            lw=1.2,
            zorder=3,
            label=region,
        )
    nudges = {
        "W.P. Putrajaya": (0, -10),
        "Selangor": (4, 4),
        "W.P. Labuan": (4, 4),
        "Negeri Sembilan": (4, -8),
        "Pahang": (4, 4),
        "Perak": (-28, 6),
    }
    for name, row in states.iterrows():
        short = name.replace("W.P. ", "")
        ax.annotate(
            short,
            (row.float_slope, row.shock_rise_pct),
            xytext=nudges.get(name, (5, 2)),
            textcoords="offset points",
            fontsize=8.5,
            color=INK2,
        )
    ax.set_xlabel(
        "Sensitivity under the weekly float, 2017-21\n(transport CPI % per 1% RON95 change)"
    )
    ax.set_ylabel("Transport CPI rise, Feb to Sep 2026 (%)")
    ax.set_title("Under BUDI95 the cities, not the rural states, felt the 2026 shock most")
    ax.legend(loc="upper right", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "states_then_and_now.svg")
    plt.close(fig)


if __name__ == "__main__":
    main()
