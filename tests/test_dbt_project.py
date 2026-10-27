"""House rules for the dbt project that dbt itself does not enforce.

dbt happily builds a model with no description and no tests. These checks keep the Phase 3
completion criterion ("every key column has a description and at least one test") true as
models are added, without needing a database.
"""

from pathlib import Path

import pytest
import yaml

DBT_DIR = Path(__file__).resolve().parents[1] / "dbt"
MODELS_DIR = DBT_DIR / "models"


def documented_models() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in MODELS_DIR.rglob("*.yml"):
        for model in (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("models", []):
            found[model["name"]] = model
    return found


def sql_models() -> list[str]:
    return sorted(p.stem for p in MODELS_DIR.rglob("*.sql"))


def test_every_sql_model_is_documented_and_every_documented_model_exists():
    assert set(sql_models()) == set(documented_models())


@pytest.mark.parametrize("name", sql_models())
def test_model_has_a_description_and_a_stated_grain(name):
    description = documented_models()[name].get("description", "")
    assert description.strip(), f"{name} has no description"
    # Every table's grain must be statable in one sentence (Tier 3 completion criterion).
    assert "grain" in description.lower(), f"{name}'s description does not state its grain"


@pytest.mark.parametrize("name", sql_models())
def test_model_has_tests_and_every_column_is_described(name):
    model = documented_models()[name]
    column_tests = sum(len(c.get("data_tests", [])) for c in model.get("columns", []))
    assert column_tests + len(model.get("data_tests", [])) > 0, f"{name} has no tests"
    undescribed = [c["name"] for c in model.get("columns", []) if not c.get("description")]
    assert not undescribed, f"{name}: columns without a description: {undescribed}"


def test_sources_declare_freshness_on_a_data_date_not_loaded_at():
    sources = yaml.safe_load((MODELS_DIR / "staging" / "_sources.yml").read_text())
    for table in sources["sources"][0]["tables"]:
        assert "loaded_at_field" in table and "freshness" in table, table["name"]
        # loaded_at only moves when a value changes (DECISIONS.md #4), so it cannot show staleness.
        assert not table["loaded_at_field"].startswith("loaded_at"), table["name"]


def test_profile_never_contains_a_literal_password():
    profile = (DBT_DIR / "profiles.yml").read_text(encoding="utf-8")
    assert "env_var('POSTGRES_PASSWORD')" in profile
    assert "change_me" not in profile
