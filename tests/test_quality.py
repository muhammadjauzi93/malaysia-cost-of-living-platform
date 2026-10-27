from datetime import date

import pandas as pd
import pytest

from col_pipeline.quality import (
    Severity,
    blocking_failures,
    evaluate_frame,
    evaluate_freshness,
    format_failures,
)
from col_pipeline.sources.base import QualityRules, TableSpec

TABLE = TableSpec("raw", "demo", ("d", "k"), ("v", "lo", "hi"), "d")


def frame(**overrides):
    base = {
        "d": [date(2026, 1, 1), date(2026, 1, 1)],
        "k": ["a", "b"],
        "v": [1.0, 2.0],
        "lo": [1.0, 1.0],
        "hi": [2.0, 2.0],
    }
    return pd.DataFrame({**base, **overrides})


RULES = QualityRules(min_rows=2, max_age_days=7, value_ranges={"v": (0.0, 10.0)})


def by_name(results):
    return {r.name: r for r in results}


def test_a_clean_frame_passes_everything():
    results = evaluate_frame(frame(), TABLE, RULES)
    assert all(r.passed for r in results)
    assert blocking_failures(results) == []


def test_too_few_rows_is_an_error():
    results = by_name(evaluate_frame(frame().head(1), TABLE, RULES))
    assert not results["min_rows"].passed
    assert results["min_rows"].severity is Severity.ERROR


def test_missing_columns_short_circuit_with_one_clear_result():
    results = evaluate_frame(frame().drop(columns=["v"]), TABLE, RULES)
    assert [r.name for r in results] == ["columns"]
    assert "v" in results[0].detail


def test_nulls_fail_unless_the_column_is_declared_nullable():
    with_null = frame(v=[1.0, None])
    assert not by_name(evaluate_frame(with_null, TABLE, RULES))["not_null"].passed
    lenient = QualityRules(min_rows=2, max_age_days=7, nullable_columns=("v",))
    assert by_name(evaluate_frame(with_null, TABLE, lenient))["not_null"].passed


def test_duplicate_keys_fail():
    dupes = frame(k=["a", "a"])
    assert not by_name(evaluate_frame(dupes, TABLE, RULES))["unique_key"].passed


@pytest.mark.parametrize("bad", [-1.0, 11.0])
def test_out_of_range_values_fail_and_show_an_example(bad):
    result = by_name(evaluate_frame(frame(v=[1.0, bad]), TABLE, RULES))["range:v"]
    assert not result.passed
    assert str(bad) in result.detail


def test_ordered_pairs_catch_inverted_values_and_ignore_nulls():
    rules = QualityRules(min_rows=2, max_age_days=7, ordered_pairs=(("lo", "hi"),))
    inverted = frame(lo=[3.0, 1.0])  # first row: lo 3 > hi 2
    assert not by_name(evaluate_frame(inverted, TABLE, rules))["order:lo<=hi"].passed
    with_null = frame(lo=[None, 1.0])
    assert by_name(evaluate_frame(with_null, TABLE, rules))["order:lo<=hi"].passed


def test_required_values_are_a_warning_not_a_block():
    rules = QualityRules(min_rows=2, max_age_days=7, required_values={"k": ("a", "b", "c")})
    result = by_name(evaluate_frame(frame(), TABLE, rules))["complete:k"]
    assert not result.passed
    assert result.severity is Severity.WARN
    assert "c" in result.detail
    assert blocking_failures([result]) == []


def test_group_size_is_a_warning():
    rules = QualityRules(min_rows=2, max_age_days=7, group_size=("d", 3))
    result = by_name(evaluate_frame(frame(), TABLE, rules))["group_size:d"]
    assert not result.passed and result.severity is Severity.WARN


def test_format_failures_lists_only_failures():
    results = evaluate_frame(frame(v=[1.0, 99.0]), TABLE, RULES)
    text = format_failures(results)
    assert "range:v" in text and "min_rows" not in text


# ---- freshness ----

TODAY = date(2026, 10, 26)


def test_freshness_passes_within_the_limit():
    assert evaluate_freshness(date(2026, 10, 19), RULES, TODAY).passed


def test_freshness_fails_beyond_the_limit_and_says_by_how_much():
    result = evaluate_freshness(date(2026, 10, 1), RULES, TODAY)
    assert not result.passed and result.severity is Severity.ERROR
    assert "25 day(s) old" in result.detail


def test_freshness_fails_on_an_empty_table():
    result = evaluate_freshness(None, RULES, TODAY)
    assert not result.passed and "never loaded" in result.detail
