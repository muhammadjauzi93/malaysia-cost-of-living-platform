"""Draw the dbt lineage graph as an SVG, straight from dbt's own manifest.

Why generate it: a screenshot of `dbt docs serve` goes stale the first time a model changes, and
nobody notices. This reads `dbt/target/manifest.json` (written by any dbt parse, build or run),
lays the project out in one column per layer, and writes `docs/lineage.svg`. The output is
deterministic, so `--check` can fail CI when the committed diagram no longer matches the models.

Standard library only, so it runs before or without anything else installed.

    python -m col_pipeline.lineage            # write docs/lineage.svg
    python -m col_pipeline.lineage --check    # exit 1 if docs/lineage.svg is out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "dbt" / "target" / "manifest.json"
OUTPUT = ROOT / "docs" / "lineage.svg"
PROJECT = "cost_of_living"

# Columns, left to right: (title, box fill, column tint, column stroke).
# Seeds share the staging column (at the bottom) rather than sitting between raw and staging,
# where every raw-to-staging line would pass behind them and look like it flowed through them.
COLUMNS = [
    ("RAW SOURCES", "#3b82f6", "#eff6ff", "#3b82f6"),
    ("STAGING AND SEEDS", "#10b981", "#ecfdf5", "#10b981"),
    ("INTERMEDIATE", "#0d9488", "#f0fdfa", "#14b8a6"),
    ("DIMENSIONS", "#7c3aed", "#f5f3ff", "#8b5cf6"),
    ("FACTS", "#f59e0b", "#fffbeb", "#f59e0b"),
]
SNAPSHOT_FILL = "#dc2626"  # snapshot: distinct colour inside the staging column
SEED_FILL = "#64748b"  # seeds: grey, at the bottom of the staging column
EDGE = "#94a3b8"

BOX_W, BOX_H, ROW_GAP = 220, 36, 14
COL_GAP, COL_PAD, TOP = 60, 18, 120
LEFT = 30


@dataclass
class Node:
    uid: str
    name: str
    column: int
    kind: str  # source, seed, model, snapshot
    parents: list[str] = field(default_factory=list)
    y: float = 0.0


def column_for(kind: str, name: str, path: str) -> int:
    if kind == "source":
        return 0
    if kind in ("seed", "snapshot") or "staging" in path:
        return 1
    if "intermediate" in path:
        return 2
    return 3 if name.startswith("dim_") else 4


def load_nodes(manifest: dict) -> dict[str, Node]:
    """Project sources, seeds, models and snapshots, with their parents inside the project."""
    nodes: dict[str, Node] = {}
    for uid, src in manifest.get("sources", {}).items():
        if src.get("package_name") == PROJECT:
            nodes[uid] = Node(uid, f"{src['source_name']}.{src['name']}", 0, "source")
    for uid, node in manifest.get("nodes", {}).items():
        kind = node.get("resource_type")
        if node.get("package_name") != PROJECT or kind not in ("model", "seed", "snapshot"):
            continue
        path = node.get("original_file_path", "")
        nodes[uid] = Node(uid, node["name"], column_for(kind, node["name"], path), kind)
    for uid, node in manifest.get("nodes", {}).items():
        if uid in nodes:
            parents = node.get("depends_on", {}).get("nodes", [])
            nodes[uid].parents = sorted(p for p in parents if p in nodes)
    return nodes


def place(nodes: dict[str, Node]) -> dict[int, list[Node]]:
    """Order each column by the average height of its parents, which keeps lines short.

    Columns are placed left to right; a node with no parents placed yet keeps alphabetical order.
    Deterministic: ties break on the name.
    """
    columns: dict[int, list[Node]] = {c: [] for c in range(len(COLUMNS))}
    for node in nodes.values():
        columns[node.column].append(node)
    for c in range(len(COLUMNS)):
        col = columns[c]

        def key(n: Node, c: int = c) -> tuple[int, float, str]:
            placed = [nodes[p].y for p in n.parents if nodes[p].column < c]
            group = 1 if n.kind == "seed" else 0  # seeds below the models in their column
            return (group, sum(placed) / len(placed) if placed else -1.0, n.name)

        col.sort(key=key)
        for i, node in enumerate(col):
            node.y = TOP + COL_PAD + 26 + i * (BOX_H + ROW_GAP)
    return columns


def col_x(c: int) -> int:
    return LEFT + c * (BOX_W + 2 * COL_PAD + COL_GAP)


def render(manifest: dict) -> str:
    nodes = load_nodes(manifest)
    columns = place(nodes)
    rows = max(len(col) for col in columns.values())
    col_h = 26 + rows * (BOX_H + ROW_GAP) + COL_PAD * 2 - ROW_GAP
    width = col_x(len(COLUMNS) - 1) + BOX_W + 2 * COL_PAD + LEFT
    height = TOP + col_h + 70

    models = sum(1 for n in nodes.values() if n.kind == "model")
    seeds = sum(1 for n in nodes.values() if n.kind == "seed")
    snaps = sum(1 for n in nodes.values() if n.kind == "snapshot")
    sources = sum(1 for n in nodes.values() if n.kind == "source")
    subtitle = (
        f"{sources} sources · {seeds} seeds · {models} models · {snaps} snapshot"
        f"{'s' if snaps != 1 else ''} · generated from dbt/target/manifest.json"
    )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" font-family="Segoe UI, Helvetica, Arial, sans-serif">',
        '  <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{EDGE}"/>'
        "</marker></defs>",
        f'  <rect width="{width}" height="{height}" rx="20" fill="#ffffff"/>',
        f'  <text x="{width / 2:.0f}" y="48" text-anchor="middle" font-size="24" '
        f'font-weight="700" fill="#0f172a">dbt lineage: raw data to dashboard tables</text>',
        f'  <text x="{width / 2:.0f}" y="76" text-anchor="middle" font-size="14" '
        f'fill="#64748b">{escape(subtitle)}</text>',
    ]

    for c, (title, _fill, tint, stroke) in enumerate(COLUMNS):
        x = col_x(c)
        out.append(
            f'  <rect x="{x}" y="{TOP}" width="{BOX_W + 2 * COL_PAD}" height="{col_h}" rx="16" '
            f'fill="{tint}" stroke="{stroke}" stroke-width="2"/>'
        )
        out.append(
            f'  <text x="{x + COL_PAD + BOX_W / 2:.0f}" y="{TOP + 26}" text-anchor="middle" '
            f'font-size="13" font-weight="700" fill="#334155">{title}</text>'
        )

    # Edges first, so boxes are drawn on top of them.
    # An arrowhead marks where a line really ends; a line passing behind a box has none there.
    out.append(f'  <g fill="none" stroke="{EDGE}" stroke-width="1.4" marker-end="url(#arrow)">')
    for node in sorted(nodes.values(), key=lambda n: (n.column, n.name)):
        for p in node.parents:
            parent = nodes[p]
            x1 = col_x(parent.column) + COL_PAD + BOX_W
            y1 = parent.y + BOX_H / 2
            x2 = col_x(node.column) + COL_PAD
            y2 = node.y + BOX_H / 2
            bend = max(40.0, (x2 - x1) / 2)
            out.append(
                f'    <path d="M{x1:.0f},{y1:.0f} C{x1 + bend:.0f},{y1:.0f} '
                f'{x2 - bend:.0f},{y2:.0f} {x2:.0f},{y2:.0f}"/>'
            )
    out.append("  </g>")

    for c in range(len(COLUMNS)):
        for node in columns[c]:
            fill = {"snapshot": SNAPSHOT_FILL, "seed": SEED_FILL}.get(node.kind, COLUMNS[c][1])
            x = col_x(c) + COL_PAD
            out.append(
                f'  <rect x="{x}" y="{node.y:.0f}" width="{BOX_W}" height="{BOX_H}" rx="10" '
                f'fill="{fill}"/>'
            )
            out.append(
                f'  <text x="{x + BOX_W / 2:.0f}" y="{node.y + BOX_H / 2 + 5:.0f}" '
                f'text-anchor="middle" font-size="13" font-weight="600" fill="#ffffff">'
                f"{escape(node.name)}</text>"
            )

    legend_y = TOP + col_h + 40
    legend = [
        (SNAPSHOT_FILL, "snapshot: keeps every revision of a price (SCD Type 2)"),
        (SEED_FILL, "seed: small hand-maintained reference list (CSV in version control)"),
    ]
    for i, (fill, label) in enumerate(legend):
        x = LEFT + 10 + i * 520
        out.append(
            f'  <rect x="{x}" y="{legend_y - 13}" width="16" height="16" rx="4" fill="{fill}"/>'
        )
        out.append(
            f'  <text x="{x + 24}" y="{legend_y}" font-size="13" fill="#334155">{label}</text>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if docs/lineage.svg is stale")
    args = parser.parse_args(argv)

    if not MANIFEST.exists():
        print(f"[FAIL] {MANIFEST} not found. Run `python tasks.py dbt parse` first.")
        return 1
    svg = render(json.loads(MANIFEST.read_text(encoding="utf-8")))

    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != svg:
            print(
                "[FAIL] docs/lineage.svg does not match the dbt models. "
                "Run `python tasks.py lineage` and commit the result."
            )
            return 1
        print("[ OK ] docs/lineage.svg matches the dbt models")
        return 0

    OUTPUT.write_text(svg, encoding="utf-8", newline="\n")
    print(f"[ OK ] wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
