import math
from datetime import date

import pandas as pd
import pytest

from col_pipeline.load import build_upsert_statements, frame_to_records
from col_pipeline.sources import SOURCES, get_source
from col_pipeline.sources.base import TableSpec


def test_statements_are_derived_from_the_table_spec():
    table = TableSpec("raw", "demo", ("d", "k"), ("v", "w"), "d")
    s = build_upsert_statements(table)
    assert s.create_stage == (
        "CREATE TEMP TABLE stg_demo (LIKE raw.demo INCLUDING DEFAULTS) ON COMMIT DROP"
    )
    assert s.insert_stage == "INSERT INTO stg_demo (d, k, v, w) VALUES %s"
    assert (
        "ON CONFLICT (d, k) DO UPDATE SET v = EXCLUDED.v, w = EXCLUDED.w, loaded_at = now()"
        in s.merge
    )
    # Only rewrite a row when a value really changed, comparing all value columns at once
    assert "WHERE (t.v, t.w) IS DISTINCT FROM (EXCLUDED.v, EXCLUDED.w)" in s.merge
    assert s.merge.endswith("RETURNING (xmax = 0) AS inserted")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"schema": "raw; DROP TABLE x", "table": "t"},
        {"schema": "raw", "table": "T"},
        {"schema": "raw", "table": "t", "key_columns": ("d", "bad name")},
    ],
)
def test_table_spec_rejects_unsafe_identifiers(kwargs):
    base = {
        "schema": "raw",
        "table": "t",
        "key_columns": ("d",),
        "value_columns": ("v",),
        "date_column": "d",
    }
    with pytest.raises(ValueError, match="identifier"):
        TableSpec(**{**base, **kwargs})


def test_table_spec_rejects_overlapping_or_missing_keys():
    with pytest.raises(ValueError, match="both key and value"):
        TableSpec("raw", "t", ("d",), ("d",), "d")
    with pytest.raises(ValueError, match="primary key"):
        TableSpec("raw", "t", ("k",), ("v",), "d")


def test_every_registered_source_builds_valid_statements():
    for source in SOURCES.values():
        assert build_upsert_statements(source.table).merge


def test_frame_to_records_turns_nan_into_none():
    df = pd.DataFrame({"d": [date(2026, 1, 1)], "x": [float("nan")], "y": [1.5]})
    (row,) = frame_to_records(df)
    assert row["x"] is None and row["y"] == 1.5 and not math.isnan(row["y"])


def test_registry_lookup_has_a_helpful_error():
    assert get_source("fuel_prices").table.qualified == "raw.fuel_price_weekly"
    with pytest.raises(KeyError, match="Known sources"):
        get_source("nope")
