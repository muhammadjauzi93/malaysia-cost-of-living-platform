"""The `col-pipeline` command. Same commands on Windows, macOS and Linux.

    col-pipeline run [--full-refresh]   load fuel prices
    col-pipeline runs [-n 10]           show recent pipeline runs
    col-pipeline check                  diagnose the database connection and schema

Also runnable as `python -m col_pipeline.cli ...`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import create_engine, text

from col_pipeline import main as pipeline
from col_pipeline.config import Settings

RUN_COLUMNS = [
    "run_id",
    "status",
    "started_at",
    "watermark_from",
    "rows_extracted",
    "rows_inserted",
    "rows_updated",
    "error_message",
]
REQUIRED_TABLES = ["raw.fuel_price_weekly", "ops.pipeline_runs"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="col-pipeline",
        description="Malaysia Cost-of-Living Data Platform: Phase 1 ingestion.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    run_p = sub.add_parser("run", help="Load fuel prices (incremental by default).")
    run_p.add_argument(
        "--full-refresh",
        action="store_true",
        help="Ignore the watermark and re-pull the full history.",
    )

    runs_p = sub.add_parser("runs", help="Show recent pipeline runs.")
    runs_p.add_argument("-n", "--limit", type=int, default=10, help="How many runs (default 10).")

    sub.add_parser("check", help="Check the database connection and schema.")
    return parser


def format_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Render rows as a fixed-width text table. Pure, so it is unit-testable."""
    if not rows:
        return "(no runs yet: try `col-pipeline run --full-refresh`)"

    def cell(value: Any) -> str:
        if value is None:
            return ""
        text_value = str(value).replace("\n", " ")
        return text_value if len(text_value) <= 60 else text_value[:57] + "..."

    body = [[cell(row.get(col)) for col in columns] for row in rows]
    widths = [max(len(col), *(len(r[i]) for r in body)) for i, col in enumerate(columns)]
    lines = [
        "  ".join(col.ljust(w) for col, w in zip(columns, widths, strict=True)),
        "  ".join("-" * w for w in widths),
    ]
    lines += ["  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)) for r in body]
    return "\n".join(line.rstrip() for line in lines)


def show_runs(limit: int) -> int:
    engine = create_engine(Settings.from_env().db_url)
    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        f"SELECT {', '.join(RUN_COLUMNS)} FROM ops.pipeline_runs "
                        "ORDER BY run_id DESC LIMIT :limit"
                    ),
                    {"limit": limit},
                )
                .mappings()
                .all()
            )
    finally:
        engine.dispose()
    print(format_table(rows, RUN_COLUMNS))
    return 0


def check() -> int:
    """Friendly first diagnostic for 'it doesn't work on my machine'."""
    try:
        settings = Settings.from_env()
    except RuntimeError as exc:
        print(f"[FAIL] Configuration: {exc}")
        return 1
    print("[ OK ] Configuration loaded")

    engine = create_engine(settings.db_url, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            print("[ OK ] Connected to Postgres")
            missing = [
                t
                for t in REQUIRED_TABLES
                if conn.execute(text("SELECT to_regclass(:t)"), {"t": t}).scalar() is None
            ]
    except Exception as exc:  # any driver error -> one readable hint
        print(f"[FAIL] Cannot connect: {type(exc).__name__}")
        print("       Is Postgres running? Try `python tasks.py up`, then check .env.")
        return 1
    finally:
        engine.dispose()

    if missing:
        print(f"[FAIL] Missing tables: {', '.join(missing)}")
        print("       The schema runs only on a fresh volume. Try `python tasks.py reset-db`.")
        return 1
    print("[ OK ] Schema present")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return pipeline.run(["--full-refresh"] if args.full_refresh else [])
    if args.command == "runs":
        return show_runs(args.limit)
    if args.command == "check":
        return check()
    raise AssertionError(f"unhandled command {args.command!r}")  # argparse prevents this


if __name__ == "__main__":
    sys.exit(main())
