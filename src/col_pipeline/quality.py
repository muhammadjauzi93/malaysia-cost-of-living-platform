"""Data quality checks, as pure functions.

Two kinds, applied at two moments:

* `evaluate_frame` judges a transformed DataFrame *before* it is loaded. Any failed ERROR-level
  check blocks the load (the "quality gate").
* `evaluate_freshness` judges what is *in the table* afterwards: has the source stalled?

Both return `CheckResult`s instead of raising, so the CLI, the Dagster asset checks and the
unit tests all consume the same objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

import pandas as pd

from col_pipeline.sources.base import QualityRules, TableSpec

KL = ZoneInfo("Asia/Kuala_Lumpur")


class Severity(StrEnum):
    ERROR = "error"  # blocks the load
    WARN = "warn"  # reported, never blocks


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    severity: Severity
    detail: str


def kl_today() -> date:
    """Today's date in Kuala Lumpur. Freshness is judged on the local calendar, not UTC."""
    return datetime.now(KL).date()


def _sample(df: pd.DataFrame, columns: list[str], n: int = 3) -> str:
    """A few offending rows as plain text, so dates read 2026-07-01 and not datetime.date(...)."""
    return str(df[columns].head(n).astype(str).to_dict("records"))


def evaluate_frame(df: pd.DataFrame, table: TableSpec, rules: QualityRules) -> list[CheckResult]:
    """Run every applicable check. Missing columns short-circuit: later checks need them."""
    results: list[CheckResult] = []
    err, warn = Severity.ERROR, Severity.WARN

    missing = [c for c in table.columns if c not in df.columns]
    if missing:
        return [CheckResult("columns", False, err, f"missing columns: {missing}")]
    results.append(CheckResult("columns", True, err, "all expected columns present"))

    results.append(
        CheckResult(
            "min_rows",
            len(df) >= rules.min_rows,
            err,
            f"{len(df)} rows (minimum {rules.min_rows})",
        )
    )

    required = [c for c in table.columns if c not in rules.nullable_columns]
    null_counts = {c: int(df[c].isna().sum()) for c in required}
    nulls = {c: n for c, n in null_counts.items() if n}
    results.append(
        CheckResult("not_null", not nulls, err, f"nulls found: {nulls}" if nulls else "no nulls")
    )

    dupes = int(df.duplicated(subset=list(table.key_columns)).sum())
    results.append(
        CheckResult(
            "unique_key",
            dupes == 0,
            err,
            f"{dupes} duplicate keys on {list(table.key_columns)}" if dupes else "keys unique",
        )
    )

    for column, (low, high) in rules.value_ranges.items():
        present = df[df[column].notna()]
        bad = present[~present[column].between(low, high)]
        detail = (
            f"{len(bad)} values outside [{low}, {high}], e.g. {_sample(bad, list(table.columns))}"
            if len(bad)
            else f"all within [{low}, {high}]"
        )
        results.append(CheckResult(f"range:{column}", bad.empty, err, detail))

    for lower, upper in rules.ordered_pairs:
        both = df[df[lower].notna() & df[upper].notna()]
        bad = both[both[lower] > both[upper]]
        detail = (
            f"{len(bad)} rows where {lower} > {upper}, e.g. {_sample(bad, list(table.columns))}"
            if len(bad)
            else f"{lower} <= {upper} everywhere"
        )
        results.append(CheckResult(f"order:{lower}<={upper}", bad.empty, err, detail))

    for column, expected in rules.required_values.items():
        absent = sorted(set(expected) - set(df[column]))
        detail = f"missing {column} values: {absent}" if absent else f"all {column} values present"
        results.append(CheckResult(f"complete:{column}", not absent, warn, detail))

    if rules.group_size is not None:
        column, expected_size = rules.group_size
        sizes = df.groupby(column).size()
        off = sizes[sizes != expected_size]
        detail = (
            f"{len(off)} {column} group(s) not {expected_size} rows, e.g. "
            f"{ {str(k): int(v) for k, v in off.head(3).items()} }"
            if len(off)
            else f"every {column} group has {expected_size} rows"
        )
        results.append(CheckResult(f"group_size:{column}", off.empty, warn, detail))

    return results


def evaluate_freshness(latest: date | None, rules: QualityRules, today: date) -> CheckResult:
    """Has the newest loaded date fallen further behind than the source's cadence allows?"""
    if latest is None:
        return CheckResult("freshness", False, Severity.ERROR, "table is empty: never loaded")
    age = (today - latest).days
    return CheckResult(
        "freshness",
        age <= rules.max_age_days,
        Severity.ERROR,
        f"newest date {latest} is {age} day(s) old (limit {rules.max_age_days})",
    )


def blocking_failures(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if not r.passed and r.severity is Severity.ERROR]


def format_failures(results: list[CheckResult]) -> str:
    return "; ".join(f"{r.name}: {r.detail}" for r in results if not r.passed)
