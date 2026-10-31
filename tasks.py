"""Cross-platform task runner. Replaces the old Makefile; standard library only.

    python tasks.py              list tasks
    python tasks.py setup        create .venv and .env (uses uv if installed, else venv + pip)
    python tasks.py up           start Postgres
    python tasks.py run          ...and so on

Works identically in PowerShell, cmd, bash and zsh. Tasks that need the project's packages
run inside .venv automatically, so you never have to activate it or type a .venv path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from collections.abc import Callable, Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Checked before importing tomllib (3.11+), so old interpreters get a clear message.
if sys.version_info < (3, 11):  # noqa: UP036
    sys.exit(f"Python 3.11+ is required; this is {sys.version.split()[0]}.")

import tomllib  # noqa: E402

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"

TASKS: dict[str, Callable[[list[str]], int]] = {}


def task(fn: Callable[[list[str]], int]) -> Callable[[list[str]], int]:
    """Register a task under its function name: reset_db -> reset-db, help_ -> help."""
    TASKS[fn.__name__.strip("_").replace("_", "-")] = fn
    return fn


# ---------- helpers ----------


def venv_python_path(venv_dir: Path, os_name: str = os.name) -> Path:
    """Where the venv's interpreter lives. The one line that differs between Windows and Unix."""
    if os_name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def project_python() -> str:
    """The interpreter that has the project installed.

    Inside an active venv (or under `uv run`) that is the current interpreter. Otherwise use
    ./.venv, so `python tasks.py test` works without activating anything.
    """
    if sys.prefix != sys.base_prefix:
        return sys.executable
    candidate = venv_python_path(VENV_DIR)
    if candidate.exists():
        return str(candidate)
    sys.exit("No .venv found. Run `python tasks.py setup` first.")


def sh(*cmd: str, env: dict[str, str] | None = None) -> int:
    """Run a command from the repo root. Args are a list, so there is no shell quoting to break."""
    print("$", " ".join(cmd), flush=True)
    try:
        return subprocess.call(list(cmd), cwd=ROOT, env={**os.environ, **(env or {})})
    except FileNotFoundError:
        print(f"Command not found: {cmd[0]}. Is it installed and on your PATH?")
        return 127


def py(*args: str, env: dict[str, str] | None = None) -> int:
    """Run something with the project's interpreter, e.g. py("-m", "pytest")."""
    return sh(project_python(), *args, env=env)


def group_dependencies(group: str) -> list[str]:
    """Read a dependency group from pyproject.toml, so it is declared in exactly one place."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["dependency-groups"][group]


def dev_dependencies() -> list[str]:
    return group_dependencies("dev")


def ensure_env_file() -> None:
    env_file, example = ROOT / ".env", ROOT / ".env.example"
    if env_file.exists():
        return
    shutil.copyfile(example, env_file)
    print("Created .env from .env.example. Change POSTGRES_PASSWORD in .env before `up`.")


# ---------- tasks ----------


@task
def setup(args: list[str]) -> int:
    """Create .env and .venv, install the project and dev tools. Add --pip to skip uv."""
    ensure_env_file()
    if shutil.which("uv") and "--pip" not in args:
        return sh("uv", "sync")

    print("Using venv + pip (uv not found or --pip given).")
    if not VENV_DIR.exists():
        print(f"Creating {VENV_DIR.name} with Python {sys.version.split()[0]}")
        venv.create(VENV_DIR, with_pip=True)
    python = str(venv_python_path(VENV_DIR))
    return (
        sh(python, "-m", "pip", "install", "--upgrade", "pip")
        or sh(python, "-m", "pip", "install", "-e", ".")
        or sh(python, "-m", "pip", "install", *dev_dependencies(), *group_dependencies("dbt"))
    )


@task
def up(args: list[str]) -> int:
    """Start Postgres and wait until it is healthy."""
    if not (ROOT / ".env").exists():
        print("No .env yet. Run `python tasks.py setup` first.")
        return 1
    return sh("docker", "compose", "up", "-d", "--wait")


@task
def down(args: list[str]) -> int:
    """Stop Postgres, keep its data."""
    return sh("docker", "compose", "down")


@task
def reset_db(args: list[str]) -> int:
    """Delete the database volume and start fresh (re-runs schema.sql). Add --yes to skip asking."""
    if "--yes" not in args:
        answer = input("This deletes ALL loaded data. Type 'yes' to continue: ")
        if answer.strip().lower() != "yes":
            print("Cancelled.")
            return 1
    return sh("docker", "compose", "down", "-v") or up([])


@task
def psql(args: list[str]) -> int:
    """Open psql inside the container (no local psql needed)."""
    # sh -c runs *inside* the Linux container, so this quoting is the same on every host OS.
    return sh("docker", "compose", "exec", "postgres", "sh", "-c",
              'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"')  # fmt: skip


@task
def init_db(args: list[str]) -> int:
    """Create any missing tables in an existing database (safe to repeat)."""
    return py("-m", "col_pipeline.cli", "init-db")


@task
def sources(args: list[str]) -> int:
    """List the data sources and the tables they load."""
    return py("-m", "col_pipeline.cli", "sources")


@task
def probe_bnm(args: list[str]) -> int:
    """Call the BNM API and save exactly what it returns under data/probe/."""
    return py("-m", "col_pipeline.cli", "probe-bnm")


@task
def check(args: list[str]) -> int:
    """Diagnose config, database connection and schema."""
    return py("-m", "col_pipeline.cli", "check")


@task
def run(args: list[str]) -> int:
    """Load sources, incrementally. `run fx_rates` for one; `run --since 2026-10-01` to backfill."""
    return py("-m", "col_pipeline.cli", "run", *args)


@task
def full_refresh(args: list[str]) -> int:
    """Re-pull the full history of every source (or name some: `full-refresh cpi_state`)."""
    return py("-m", "col_pipeline.cli", "run", "--full-refresh", *args)


# Mirrors col_pipeline.sources.SOURCES. tasks.py cannot import the project (it runs before
# `setup` installs it), so a test keeps the two lists in sync.
SOURCE_NAMES = ("fuel_prices", "cpi_state", "fx_rates")
DAGSTER_MODULE = "col_pipeline.orchestration.definitions"
DAGSTER_HOME = ROOT / "dagster_home"


def dagster_env() -> dict[str, str]:
    """Dagster keeps schedule state, run history and logs under DAGSTER_HOME.

    Pinning it inside the repo (and gitignoring its contents) means schedules survive a restart
    instead of vanishing with a temporary directory. dagster.yaml in that folder is committed.
    """
    DAGSTER_HOME.mkdir(exist_ok=True)
    return {"DAGSTER_HOME": str(DAGSTER_HOME)}


def prepare_dbt_manifest() -> int:
    """Dagster reads the dbt models from dbt's manifest, so make sure it exists and is current.

    Installs dbt packages the first time, then `dbt parse` (no database query; seconds).
    """
    if not dbt_packages_installed() and (code := run_dbt("deps")):
        return code
    return run_dbt("parse", "--quiet")


@task
def dagster(args: list[str]) -> int:
    """Start the Dagster UI and scheduler at http://localhost:3000 (Ctrl+C to stop)."""
    return prepare_dbt_manifest() or py(
        "-m", "dagster", "dev", "-m", DAGSTER_MODULE, *args, env=dagster_env()
    )


@task
def materialize(args: list[str]) -> int:
    """Run a job once, headless: `materialize fuel_prices`, or `warehouse` for the dbt build."""
    names = args or list(SOURCE_NAMES)
    jobs = {**{n: f"{n}_job" for n in SOURCE_NAMES}, "warehouse": "dbt_build_job"}
    for name in names:
        if name not in jobs:
            print(f"Unknown job {name!r}. Choose from: {', '.join(jobs)}")
            return 2
    if code := prepare_dbt_manifest():
        return code
    for name in names:
        code = py("-m", "dagster", "job", "execute", "-m", DAGSTER_MODULE,
                  "-j", jobs[name], env=dagster_env())  # fmt: skip
        if code:
            return code
    return 0


@task
def monitor(args: list[str]) -> int:
    """Run the freshness monitor once: fails if any table has gone stale."""
    return prepare_dbt_manifest() or py("-m", "dagster", "job", "execute", "-m", DAGSTER_MODULE,
                                        "-j", "quality_monitor_job", env=dagster_env())  # fmt: skip


@task
def validate(args: list[str]) -> int:
    """Check that Dagster can load every definition, dbt included (CI runs this too)."""
    return prepare_dbt_manifest() or py(
        "-m", "dagster", "definitions", "validate", "-m", DAGSTER_MODULE, env=dagster_env()
    )


# ---------- dbt ----------

DBT_DIR = ROOT / "dbt"
# Calling the entry point directly avoids the RuntimeWarning that `python -m dbt.cli.main` prints.
DBT_ENTRY = "from dbt.cli.main import cli; cli(prog_name='dbt')"


def read_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE, comments, optional quotes), standard library only.

    tasks.py cannot import python-dotenv because it runs before `setup` has installed anything.
    """
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()  # inline comment on an unquoted value
        values[key] = value
    return values


def dbt_env(
    environ: Mapping[str, str] | None = None, env_file: Path | None = None
) -> dict[str, str]:
    """Environment variables dbt's profiles.yml reads, built from .env or DB_URL.

    Same precedence as the pipeline (config.db_url_from_env): a real environment variable beats
    .env, and an explicit DB_URL beats the POSTGRES_* variables. Returns only what dbt needs.
    """
    values = {
        **read_dotenv(env_file or ROOT / ".env"),
        **(os.environ if environ is None else environ),
    }

    if url := values.get("DB_URL"):
        parts = urlsplit(url)
        pg = {
            "POSTGRES_HOST": parts.hostname or "localhost",
            "POSTGRES_PORT": str(parts.port or 5432),  # a URL with no port means Postgres' own 5432
            "POSTGRES_USER": unquote(parts.username or ""),
            "POSTGRES_PASSWORD": unquote(parts.password or ""),
            "POSTGRES_DB": parts.path.lstrip("/"),
        }
    else:
        pg = {
            "POSTGRES_HOST": values.get("POSTGRES_HOST") or "localhost",
            "POSTGRES_PORT": values.get("POSTGRES_PORT") or "5433",  # host side of docker compose
            "POSTGRES_USER": values.get("POSTGRES_USER", ""),
            "POSTGRES_PASSWORD": values.get("POSTGRES_PASSWORD", ""),
            "POSTGRES_DB": values.get("POSTGRES_DB", ""),
        }
    if missing := [k for k in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB") if not pg[k]]:
        raise RuntimeError(
            f"Database settings missing: {', '.join(missing)}. "
            "Run `python tasks.py setup` to create .env, or set DB_URL."
        )
    return {**pg, "DBT_PROJECT_DIR": str(DBT_DIR), "DBT_PROFILES_DIR": str(DBT_DIR)}


def run_dbt(*args: str) -> int:
    """Run dbt inside the project venv with the database settings filled in."""
    try:
        env = dbt_env()
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1
    return py("-c", DBT_ENTRY, *args, env=env)


def dbt_packages_installed() -> bool:
    return (DBT_DIR / "dbt_packages").is_dir()


@task
def dbt(args: list[str]) -> int:
    """Run any dbt command with the project wired up: `dbt build`, `dbt test -s stg_fx_rates`."""
    return run_dbt(*args)


@task
def dbt_build(args: list[str]) -> int:
    """Install dbt packages if missing, then build every model and run every test."""
    if not dbt_packages_installed() and (code := run_dbt("deps")):
        return code
    return run_dbt("build", *args)


@task
def dbt_freshness(args: list[str]) -> int:
    """Check that every raw source is fresh enough (limits are in dbt/models/staging)."""
    return run_dbt("source", "freshness", *args)


@task
def dbt_docs(args: list[str]) -> int:
    """Generate the dbt docs site and serve it at http://localhost:8080 (Ctrl+C to stop)."""
    if not dbt_packages_installed() and (code := run_dbt("deps")):
        return code
    return run_dbt("docs", "generate") or run_dbt("docs", "serve", "--port", "8080", *args)


@task
def lineage(args: list[str]) -> int:
    """Redraw docs/lineage.svg from dbt's manifest (`--check` fails if it is out of date)."""
    return py("-m", "col_pipeline.lineage", *args)


@task
def load_sample(args: list[str]) -> int:
    """Load SYNTHETIC data into an EMPTY database, for CI and dbt work (refuses otherwise)."""
    return py("-m", "col_pipeline.cli", "load-sample", *args)


@task
def runs(args: list[str]) -> int:
    """Show recent pipeline runs. Pass -n 20 for more."""
    return py("-m", "col_pipeline.cli", "runs", *args)


@task
def test(args: list[str]) -> int:
    """Run tests. Extra args go to pytest, e.g. `python tasks.py test -k transform`."""
    return py("-m", "pytest", *args)


@task
def lint(args: list[str]) -> int:
    """Check linting and formatting without changing files."""
    return py("-m", "ruff", "check", ".") or py("-m", "ruff", "format", "--check", ".")


@task
def fmt(args: list[str]) -> int:
    """Auto-fix lint issues and format the code."""
    return py("-m", "ruff", "check", "--fix", ".") or py("-m", "ruff", "format", ".")


@task
def help_(args: list[str]) -> int:
    """List tasks."""
    width = max(map(len, TASKS))
    print("Usage: python tasks.py <task> [args]\n")
    for name, fn in TASKS.items():
        print(f"  {name.ljust(width)}  {(fn.__doc__ or '').strip().splitlines()[0]}")
    return 0


def main(argv: list[str]) -> int:
    name, *rest = argv or ["help"]
    if name not in TASKS:
        print(f"Unknown task: {name}\n")
        help_([])
        return 2
    return TASKS[name](rest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
