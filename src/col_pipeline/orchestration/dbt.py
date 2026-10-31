"""The dbt warehouse as Dagster assets: one graph from the API call to the dashboard tables.

dagster-dbt reads dbt's manifest and turns every model, seed and snapshot into a Dagster asset,
and every dbt test into an asset check. Two things are customised here:

1. dbt's sources (`raw.fuel_price_weekly`, ...) are mapped onto the ingestion assets that
   actually write those tables (`fuel_prices_raw`, ...). Without this the graph would be two
   disconnected halves, and "refresh the warehouse after ingestion" could not be expressed.
2. dbt connects with the SAME database settings as the pipeline resource, taken from it at run
   time, so dbt and the Python loader can never point at different databases (tests rely on it).

See DECISIONS.md #51.
"""

# No `from __future__ import annotations` here, matching assets.py: Dagster inspects the
# `context` annotation of asset functions, and postponed (string) annotations break that check.

import os
import shutil
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import dagster as dg
from dagster import AssetExecutionContext
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, dbt_assets
from sqlalchemy.engine import make_url

from col_pipeline.orchestration.assets import raw_name
from col_pipeline.orchestration.resources import PipelineResource
from col_pipeline.sources import SOURCES
from col_pipeline.sources.base import Source

ROOT = Path(__file__).resolve().parents[3]
DBT_DIR = ROOT / "dbt"
MANIFEST = DBT_DIR / "target" / "manifest.json"
DBT_ASSETS_NAME = "dbt_warehouse"
DBT_BUILD_JOB = "dbt_build_job"

# dbt schema -> Dagster group, so the UI shows the warehouse layer by layer.
GROUPS = {
    "staging": "warehouse_staging",
    "intermediate": "warehouse_intermediate",
    "marts": "warehouse_marts",
    "reference": "warehouse_reference",
    "snapshots": "warehouse_snapshots",
}


class ManifestMissingError(RuntimeError):
    """Dagster cannot describe the dbt assets without dbt's manifest."""


def raw_asset_keys(sources: Mapping[str, Source]) -> dict[str, str]:
    """`raw.fuel_price_weekly` -> `fuel_prices_raw`: which ingestion asset writes each raw table."""
    return {s.table.qualified: raw_name(s) for s in sources.values()}


class WarehouseTranslator(DagsterDbtTranslator):
    """Connects dbt sources to the ingestion assets and groups dbt assets by layer."""

    def __init__(self, raw_keys: Mapping[str, str]) -> None:
        super().__init__()
        self._raw_keys = dict(raw_keys)

    def get_asset_spec(
        self, manifest: Mapping[str, Any], unique_id: str, project: Any
    ) -> dg.AssetSpec:
        spec = super().get_asset_spec(manifest, unique_id, project)
        props = self.get_resource_props(manifest, unique_id)
        if props["resource_type"] == "source":
            qualified = f"{props['schema']}.{props['name']}"
            if qualified in self._raw_keys:
                return spec.replace_attributes(key=dg.AssetKey(self._raw_keys[qualified]))
            return spec
        group = GROUPS.get(props.get("schema", ""))
        return spec.replace_attributes(group_name=group) if group else spec


def postgres_env(db_url: str) -> dict[str, str]:
    """The POSTGRES_* variables dbt's profiles.yml reads, from a SQLAlchemy URL.

    tasks.py has a standard-library twin (dbt_env) because it runs before anything is installed;
    here the project is installed, so SQLAlchemy's own parser handles escaping.
    """
    url = make_url(db_url)
    return {
        "POSTGRES_HOST": url.host or "localhost",
        "POSTGRES_PORT": str(url.port or 5432),
        "POSTGRES_USER": url.username or "",
        "POSTGRES_PASSWORD": url.password or "",
        "POSTGRES_DB": url.database or "",
    }


@contextmanager
def temporary_environ(values: Mapping[str, str]) -> Iterator[None]:
    """Set environment variables for the duration of the block, then restore the old ones.

    The dbt CLI resource hands the current environment to the dbt process it starts and has no
    `env` argument, so the variables must be present at the moment that process starts.
    """
    saved = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def build_dbt_assets(
    manifest: Path = MANIFEST, sources: Mapping[str, Source] | None = None
) -> dg.AssetsDefinition:
    """Every dbt model, seed and snapshot as one multi-asset, with dbt tests as asset checks.

    A run that selects only some of these assets runs `dbt build` for just those (dagster-dbt
    passes the selection to dbt). No retry policy: a failed dbt test is deterministic, and
    retrying it would only repeat the same failure (DECISIONS.md #20).
    """
    if not manifest.exists():
        raise ManifestMissingError(
            f"{manifest} not found. Run `python tasks.py dbt parse` (the Dagster tasks in "
            "tasks.py do this for you)."
        )
    translator = WarehouseTranslator(raw_asset_keys(sources or SOURCES))

    @dbt_assets(manifest=manifest, dagster_dbt_translator=translator, name=DBT_ASSETS_NAME)
    def warehouse(
        context: AssetExecutionContext, dbt: DbtCliResource, pipeline: PipelineResource
    ) -> Iterator[Any]:
        with temporary_environ(postgres_env(pipeline.settings().db_url)):
            invocation = dbt.cli(["build"], context=context)
        yield from invocation.stream()

    return warehouse


def dbt_executable() -> str:
    """The dbt program installed alongside the Python running Dagster.

    Found by sys.executable, not PATH: `python tasks.py` starts the venv's Python directly
    without activating the venv, so the venv's Scripts (Windows) or bin folder is not on PATH,
    and dagster-dbt's default lookup of plain `dbt` fails. Found on the first real run on
    Windows; CI missed it because `uv run` puts that folder on PATH. Falls back to PATH.
    """
    name = "dbt.exe" if os.name == "nt" else "dbt"
    beside_python = Path(sys.executable).parent / name
    if beside_python.exists():
        return str(beside_python)
    return shutil.which("dbt") or "dbt"


def dbt_resource() -> DbtCliResource:
    return DbtCliResource(
        project_dir=str(DBT_DIR), profiles_dir=str(DBT_DIR), dbt_executable=dbt_executable()
    )
