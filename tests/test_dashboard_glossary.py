"""The glossary's text: complete, consistent with the app, and covering every acronym on screen.

The page itself is rendered in tests/test_dashboard_app.py with the other pages.
"""

import ast
import re
from importlib.util import find_spec
from pathlib import Path

import pytest

from col_pipeline.dashboard import analysis, glossary
from col_pipeline.dashboard.glossary import ACRONYM, DATAPOINT, TERMS, Term

DASHBOARD = Path(find_spec("col_pipeline.dashboard").origin).parent
# Date formats passed to Streamlit, not words a viewer reads.
NOT_ACRONYMS = {"MMM", "YYYY"}
ACRONYM_PATTERN = re.compile(r"\b[A-Z][A-Za-z]*[A-Z0-9]+[A-Za-z0-9]*\b")


def docstrings(tree: ast.Module) -> set[int]:
    owners = [
        n for n in ast.walk(tree) if isinstance(n, ast.Module | ast.FunctionDef | ast.ClassDef)
    ]
    return {
        id(n.body[0].value)
        for n in owners
        if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)
    }


def on_screen_strings() -> list[str]:
    """Every string literal in the pages and charts, less docstrings and colour codes, plus the
    fuel names, which the pages show."""
    files = [*sorted((DASHBOARD / "app_pages").glob("*.py")), DASHBOARD / "charts.py"]
    strings = []
    for path in files:
        if path.name in ("glossary.py", "__init__.py"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = docstrings(tree)
        strings += [
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and id(n) not in skip
            and not n.value.startswith("#")
        ]
    return strings + list(analysis._FUEL_NAMES.values())


def test_every_acronym_on_screen_has_an_entry():
    acronyms = {
        token
        for text in on_screen_strings()
        for token in ACRONYM_PATTERN.findall(text)
        if token not in NOT_ACRONYMS
    }
    assert {"CPI", "RON95", "SKDS"} <= acronyms  # the scan is finding what it should
    missing = sorted(a for a in acronyms if glossary.find(a) is None)
    assert not missing, f"add these to glossary.py: {missing}"


@pytest.mark.parametrize("key", ["pp", "YoY", "MoM", "COICOP", "BNM", "sen", "r"])
def test_units_used_in_formats_and_help_text_have_an_entry(key):
    # Too short or lower-case for the scan above, but on screen all the same.
    assert glossary.find(key) is not None


def test_entries_are_complete():
    for t in TERMS:
        assert t.term and t.meaning and t.why_it_matters and t.pages, t.term
        assert t.kind in (ACRONYM, DATAPOINT), t.term


def test_no_term_or_alias_is_listed_twice():
    keys = [k.lower() for t in TERMS for k in t.keys]
    assert len(keys) == len(set(keys))


def test_pages_named_in_entries_are_the_apps_pages():
    app = (DASHBOARD / "app.py").read_text(encoding="utf-8")
    titles = re.findall(r'st\.Page\([^)]*?title="([^"]+)"', app)
    assert titles == [*glossary.PAGES, "Glossary"]
    for t in TERMS:
        assert set(t.pages) <= set(glossary.PAGES), t.term


def test_the_owner_supplied_expansions_are_kept():
    assert glossary.find("SKDS").stands_for.startswith("Sistem Kawalan Diesel Bersubsidi")
    assert glossary.find("SKPS").stands_for.startswith("Sistem Kawalan Petrol Bersubsidi")
    assert "BUDI MADANI" in glossary.find("BUDI95").meaning


# ---------- search and grouping ----------


def test_an_empty_search_returns_everything_in_order():
    assert glossary.search("") == TERMS
    assert glossary.search("   ") == TERMS


def test_search_ignores_case_and_needs_every_word():
    found = glossary.search("PERCENTAGE points")
    assert glossary.find("pp") in found
    assert glossary.search("percentage zzz") == []


def test_search_finds_an_entry_by_its_alias():
    assert glossary.search("ron97") == [glossary.find("RON95")]


def test_find_matches_exactly_as_written():
    assert glossary.find("RON97") is glossary.find("RON95")
    assert glossary.find("cpi") is None


def test_datapoints_are_grouped_under_their_first_page_in_navigation_order():
    terms = [
        Term("b", "m", "w", ("Fuel prices",)),
        Term("a", "m", "w", ("The answer", "Fuel prices")),
        Term("x", "m", "w", ("The answer",), kind=ACRONYM),
    ]
    groups = glossary.by_page(terms)
    assert list(groups) == ["The answer", "Fuel prices"]
    assert [t.term for t in groups["The answer"]] == ["a"]
