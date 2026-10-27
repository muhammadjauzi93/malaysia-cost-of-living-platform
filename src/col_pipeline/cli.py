"""The `col-pipeline` command. Same commands on Windows, macOS and Linux.

    col-pipeline run [SOURCE ...] [--full-refresh | --since YYYY-MM-DD]
    col-pipeline runs [-n 10] [--source NAME]
    col-pipeline sources
    col-pipeline check
    col-pipeline init-db
    col-pipeline probe-bnm

Also runnable as `python -m col_pipeline.cli ...`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import create_engine, text

from col_pipeline import runner
from col_pipeline.config import Settings
from col_pipeline.extract import BNM_ACCEPT
from col_pipeline.logging_config import setup_logging
from col_pipeline.quality import kl_today
from col_pipeline.sources import SOURCES, get_source

log = logging.getLogger(__name__)

RUN_COLUMNS = [
    "run_id",
    "pipeline_name",
    "status",
    "started_at",
    "watermark_from",
    "rows_extracted",
    "rows_inserted",
    "rows_updated",
    "error_message",
]
SCHEMA_FILE = Path(__file__).resolve().parents[2] / "sql" / "schema.sql"


def required_tables() -> list[str]:
    return [*(s.table.qualified for s in SOURCES.values()), "ops.pipeline_runs"]


def source_name(value: str) -> str:
    if value not in SOURCES:
        raise argparse.ArgumentTypeError(
            f"unknown source {value!r} (choose from: {', '.join(SOURCES)})"
        )
    return value


def iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="col-pipeline",
        description="Malaysia Cost-of-Living Data Platform: ingestion.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    run_p = sub.add_parser("run", help="Load one or more sources (all of them by default).")
    run_p.add_argument(
        "sources",
        nargs="*",
        type=source_name,
        metavar="SOURCE",
        help=f"Any of: {', '.join(SOURCES)}. Omit to run all.",
    )
    mode = run_p.add_mutually_exclusive_group()
    mode.add_argument(
        "--full-refresh",
        action="store_true",
        help="Ignore the watermark and re-pull the full history.",
    )
    mode.add_argument(
        "--since",
        type=iso_date,
        metavar="YYYY-MM-DD",
        help="Backfill from this date (idempotent: safe to repeat).",
    )

    runs_p = sub.add_parser("runs", help="Show recent pipeline runs.")
    runs_p.add_argument("-n", "--limit", type=int, default=10, help="How many runs (default 10).")
    runs_p.add_argument("--source", choices=list(SOURCES), help="Only runs of one source.")

    sub.add_parser("sources", help="List the sources and where they land.")
    sub.add_parser("check", help="Check the database connection and schema.")
    sub.add_parser("init-db", help="Create any missing tables (safe to re-run).")
    sub.add_parser("probe-bnm", help="Call the BNM API and show exactly what comes back.")
    return parser


def format_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Render rows as a fixed-width text table. Pure, so it is unit-testable."""
    if not rows:
        return "(no runs yet: try `col-pipeline run`)"

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


def run_sources(
    names: Sequence[str], *, full_refresh: bool = False, since: date | None = None
) -> int:
    """Run sources one after another. One failing source never stops the others."""
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    settings = Settings.from_env()
    engine = create_engine(settings.db_url, pool_pre_ping=True)
    failed: list[str] = []
    try:
        for name in names or list(SOURCES):
            source = get_source(name)
            try:
                summary = runner.run_source(
                    source, settings, engine, full_refresh=full_refresh, since=since
                )
                print(
                    f"[ OK ] {name}: {summary.rows_inserted} inserted, "
                    f"{summary.rows_updated} updated, {summary.rows_unchanged} unchanged"
                )
            except Exception as exc:  # report, keep going, exit non-zero at the end
                log.exception("Source %s failed", name)
                print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
                failed.append(name)
    finally:
        engine.dispose()
    if failed:
        print(f"{len(failed)} source(s) failed: {', '.join(failed)}")
        return 1
    return 0


def show_runs(limit: int, source: str | None) -> int:
    engine = create_engine(Settings.from_env().db_url)
    query = (
        f"SELECT {', '.join(RUN_COLUMNS)} FROM ops.pipeline_runs "
        "WHERE (CAST(:source AS TEXT) IS NULL OR pipeline_name = :source) "
        "ORDER BY run_id DESC LIMIT :limit"
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(query), {"limit": limit, "source": source}).mappings().all()
    finally:
        engine.dispose()
    print(format_table(rows, RUN_COLUMNS))
    return 0


def show_sources() -> int:
    rows = [
        {
            "source": s.name,
            "table": s.table.qualified,
            "grain": ", ".join(s.table.key_columns),
            "lookback_days": s.lookback_days if s.incremental_from_watermark else "n/a (latest)",
            "description": s.description,
        }
        for s in SOURCES.values()
    ]
    print(format_table(rows, ["source", "table", "grain", "lookback_days", "description"]))
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
                for t in required_tables()
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
        print("       Create them with `python tasks.py init-db`.")
        return 1
    print("[ OK ] Schema present")
    return 0


def init_db() -> int:
    """Apply sql/schema.sql. Every statement is IF NOT EXISTS, so this is safe to repeat.

    Docker only runs the schema on a brand-new volume. This command is how an existing
    database picks up tables added in later phases.
    """
    if not SCHEMA_FILE.exists():
        print(f"[FAIL] {SCHEMA_FILE} not found. Run this from a checkout of the repository.")
        return 1
    engine = create_engine(Settings.from_env().db_url, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(SCHEMA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[FAIL] Could not apply the schema: {type(exc).__name__}: {exc}")
        return 1
    finally:
        engine.dispose()
    print(f"[ OK ] Schema applied. Tables: {', '.join(required_tables())}")
    return 0


def last_weekday_before(day: date) -> date:
    prev = day - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    return prev


def probe_bnm() -> int:
    """Call BNM directly and print what comes back, saving each response under data/probe/.

    BNM's endpoints are documented on a portal this project could not verify against live
    traffic during development. This command is the ground truth: if a source breaks, run
    it and compare the output with tests/fixtures/bnm_exchange_rate_sample.json.
    """
    base = os.getenv("BNM_BASE_URL", "https://api.bnm.gov.my/public").rstrip("/")
    session_code = os.getenv("BNM_SESSION", "1700")
    day = last_weekday_before(kl_today())
    probes = [
        ("latest", "exchange-rate", {"session": session_code, "quote": "rm"}),
        ("usd_latest", "exchange-rate/USD", {"session": session_code, "quote": "rm"}),
        (
            "usd_on_date",
            f"exchange-rate/USD/date/{day.isoformat()}",
            {"session": session_code, "quote": "rm"},
        ),
    ]
    out_dir = Path("data") / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    http = requests.Session()
    http.headers["Accept"] = BNM_ACCEPT

    exit_code = 0
    for label, path, params in probes:
        url = f"{base}/{path}"
        print(f"\n== {label}: GET {url} {params}")
        try:
            resp = http.get(url, params=params, timeout=30)
        except requests.RequestException as exc:
            print(f"   request failed: {type(exc).__name__}: {exc}")
            exit_code = 1
            continue
        print(f"   status {resp.status_code}, content-type {resp.headers.get('content-type')}")
        target = out_dir / f"bnm_{label}.json"
        target.write_text(resp.text, encoding="utf-8")
        print(f"   saved {len(resp.text)} bytes to {target}")
        print(f"   first 400 chars: {resp.text[:400]!r}")
        if resp.status_code >= 400:
            exit_code = 1
    print("\nShare the saved files (or paste the output) if a BNM source needs adjusting.")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        return run_sources(args.sources, full_refresh=args.full_refresh, since=args.since)
    if args.command == "runs":
        return show_runs(args.limit, args.source)
    if args.command == "sources":
        return show_sources()
    if args.command == "check":
        return check()
    if args.command == "init-db":
        return init_db()
    if args.command == "probe-bnm":
        return probe_bnm()
    raise AssertionError(f"unhandled command {args.command!r}")  # argparse prevents this


if __name__ == "__main__":
    sys.exit(main())
