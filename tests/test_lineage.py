"""The lineage diagram is generated from dbt's manifest and checked in CI, so it must be
deterministic, show only this project's nodes, and catch a stale diagram."""

import json
import re

import pytest

from col_pipeline import lineage


def manifest() -> dict:
    """A tiny manifest with the shapes dbt writes, plus things that must NOT be drawn."""
    return {
        "sources": {
            "source.cost_of_living.raw.fuel": {
                "package_name": "cost_of_living",
                "source_name": "raw",
                "name": "fuel",
            },
        },
        "nodes": {
            "model.cost_of_living.stg_fuel": {
                "resource_type": "model",
                "package_name": "cost_of_living",
                "name": "stg_fuel",
                "original_file_path": "models/staging/stg_fuel.sql",
                "depends_on": {"nodes": ["source.cost_of_living.raw.fuel"]},
            },
            "seed.cost_of_living.events": {
                "resource_type": "seed",
                "package_name": "cost_of_living",
                "name": "events",
                "original_file_path": "seeds/events.csv",
                "depends_on": {"nodes": []},
            },
            "model.cost_of_living.int_fuel": {
                "resource_type": "model",
                "package_name": "cost_of_living",
                "name": "int_fuel",
                "original_file_path": "models/intermediate/int_fuel.sql",
                "depends_on": {
                    "nodes": ["model.cost_of_living.stg_fuel", "seed.cost_of_living.events"]
                },
            },
            "model.cost_of_living.dim_x": {
                "resource_type": "model",
                "package_name": "cost_of_living",
                "name": "dim_x",
                "original_file_path": "models/marts/dim_x.sql",
                "depends_on": {"nodes": []},
            },
            "model.cost_of_living.fct_fuel": {
                "resource_type": "model",
                "package_name": "cost_of_living",
                "name": "fct_fuel",
                "original_file_path": "models/marts/fct_fuel.sql",
                "depends_on": {
                    "nodes": ["model.cost_of_living.int_fuel", "model.cost_of_living.dim_x"]
                },
            },
            "snapshot.cost_of_living.snap_fuel": {
                "resource_type": "snapshot",
                "package_name": "cost_of_living",
                "name": "snap_fuel",
                "original_file_path": "snapshots/snap_fuel.yml",
                "depends_on": {"nodes": ["source.cost_of_living.raw.fuel"]},
            },
            # Must not appear: a test, and a node from an installed package.
            "test.cost_of_living.not_null_stg_fuel_id": {
                "resource_type": "test",
                "package_name": "cost_of_living",
                "name": "nn_test",
                "depends_on": {"nodes": ["model.cost_of_living.stg_fuel"]},
            },
            "model.dbt_utils.some_helper": {
                "resource_type": "model",
                "package_name": "dbt_utils",
                "name": "some_helper",
                "original_file_path": "models/x.sql",
                "depends_on": {"nodes": []},
            },
        },
    }


def test_rendering_is_deterministic():
    assert lineage.render(manifest()) == lineage.render(json.loads(json.dumps(manifest())))


def test_only_project_sources_seeds_models_and_snapshots_are_drawn():
    svg = lineage.render(manifest())
    for name in ["raw.fuel", "stg_fuel", "events", "int_fuel", "dim_x", "fct_fuel", "snap_fuel"]:
        assert f">{name}</text>" in svg
    assert "nn_test" not in svg and "some_helper" not in svg
    assert "1 sources · 1 seeds · 4 models · 1 snapshot" in svg


def test_each_node_lands_in_its_layer_column():
    nodes = lineage.load_nodes(manifest())
    column = {n.name: n.column for n in nodes.values()}
    assert column == {
        "raw.fuel": 0,
        "stg_fuel": 1,
        "snap_fuel": 1,
        "events": 1,
        "int_fuel": 2,
        "dim_x": 3,
        "fct_fuel": 4,
    }


def test_one_edge_per_dependency_and_seeds_sit_below_models():
    nodes = lineage.load_nodes(manifest())
    svg = lineage.render(manifest())
    expected_edges = sum(len(n.parents) for n in nodes.values())
    edges = re.findall(r'<path d="M\d+,\d+ C', svg)  # edge curves only, not the arrowhead
    assert len(edges) == expected_edges == 6
    lineage.place(nodes)  # positions are assigned here, not by load_nodes
    staging = {n.name: n.y for n in nodes.values() if n.column == 1}
    assert staging["events"] > max(staging["stg_fuel"], staging["snap_fuel"])


def test_names_are_escaped():
    m = manifest()
    m["nodes"]["model.cost_of_living.dim_x"]["name"] = "dim_<x>&y"
    assert "dim_&lt;x&gt;&amp;y" in lineage.render(m)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    manifest_path, output = tmp_path / "manifest.json", tmp_path / "lineage.svg"
    manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
    monkeypatch.setattr(lineage, "MANIFEST", manifest_path)
    monkeypatch.setattr(lineage, "OUTPUT", output)
    monkeypatch.setattr(lineage, "ROOT", tmp_path)
    return manifest_path, output


def test_check_passes_when_current_and_fails_when_stale(paths, capsys):
    manifest_path, output = paths
    assert lineage.main([]) == 0
    assert lineage.main(["--check"]) == 0
    m = manifest()
    m["nodes"]["model.cost_of_living.new_model"] = {
        "resource_type": "model",
        "package_name": "cost_of_living",
        "name": "dim_new",
        "original_file_path": "models/marts/dim_new.sql",
        "depends_on": {"nodes": []},
    }
    manifest_path.write_text(json.dumps(m), encoding="utf-8")
    assert lineage.main(["--check"]) == 1
    assert "does not match" in capsys.readouterr().out


def test_missing_manifest_is_a_clear_failure(paths, capsys):
    manifest_path, _ = paths
    manifest_path.unlink()
    assert lineage.main([]) == 1
    assert "dbt parse" in capsys.readouterr().out
