"""What a data source is, as plain data.

A `Source` bundles everything the generic runner, loader, quality gate and Dagster factory
need: where the data lands (`TableSpec`), what "good data" means (`QualityRules`), and the two
source-specific functions (`fetch`, `to_frame`). Adding a source means writing one module that
builds a `Source`; nothing else changes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from col_pipeline.config import Settings

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


@dataclass(frozen=True)
class TableSpec:
    """A target table and the columns that define its grain and its values.

    Identifiers are validated because the loader interpolates them into SQL. They only ever
    come from code, never from user input, but a typo should fail at import time, not at 3am.
    """

    schema: str
    table: str
    key_columns: tuple[str, ...]
    value_columns: tuple[str, ...]
    date_column: str  # drives the incremental watermark and the freshness check

    def __post_init__(self) -> None:
        names = (self.schema, self.table, *self.key_columns, *self.value_columns, self.date_column)
        if bad := [n for n in names if not _IDENTIFIER.match(n)]:
            raise ValueError(f"Invalid SQL identifier(s) in TableSpec: {bad}")
        if overlap := set(self.key_columns) & set(self.value_columns):
            raise ValueError(f"Columns cannot be both key and value: {sorted(overlap)}")
        if self.date_column not in self.key_columns:
            raise ValueError("date_column must be part of the primary key")

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.table}"

    @property
    def columns(self) -> tuple[str, ...]:
        return (*self.key_columns, *self.value_columns)


@dataclass(frozen=True)
class QualityRules:
    """Plausibility rules for one source. See `col_pipeline.quality` for how they are applied.

    `to_frame` enforces *structure* (columns, types, unique keys) and raises. These rules judge
    *plausibility* and report, each result carrying a severity so the gate can block on errors
    while only warning about softer problems.
    """

    min_rows: int
    max_age_days: int  # freshness: newest date in the table may be at most this old
    value_ranges: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    nullable_columns: tuple[str, ...] = ()
    # (lower, upper) pairs where lower <= upper must hold whenever both are present
    ordered_pairs: tuple[tuple[str, str], ...] = ()
    # WARN-level completeness: every listed value must appear in the column
    required_values: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # WARN-level completeness: (group column, expected rows per group)
    group_size: tuple[str, int] | None = None


FetchFn = Callable[[Settings, date | None], list[dict[str, Any]]]
ToFrameFn = Callable[[list[dict[str, Any]]], pd.DataFrame]


@dataclass(frozen=True)
class Source:
    name: str
    description: str
    dataset_id: str  # landing sub-folder
    table: TableSpec
    rules: QualityRules
    fetch: FetchFn
    to_frame: ToFrameFn
    # Incremental runs re-fetch this many days behind the watermark to catch revisions.
    lookback_days: int = 0
    # False for sources whose API only serves "the latest" (BNM). They ignore the watermark
    # and only look at history when the user passes an explicit --since.
    incremental_from_watermark: bool = True
