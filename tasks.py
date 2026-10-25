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
from collections.abc import Callable
from pathlib import Path

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


def sh(*cmd: str) -> int:
    """Run a command from the repo root. Args are a list, so there is no shell quoting to break."""
    print("$", " ".join(cmd), flush=True)
    try:
        return subprocess.call(list(cmd), cwd=ROOT)
    except FileNotFoundError:
        print(f"Command not found: {cmd[0]}. Is it installed and on your PATH?")
        return 127


def py(*args: str) -> int:
    """Run something with the project's interpreter, e.g. py("-m", "pytest")."""
    return sh(project_python(), *args)


def dev_dependencies() -> list[str]:
    """Read the dev group from pyproject.toml, so it is declared in exactly one place."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["dependency-groups"]["dev"]


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
        or sh(python, "-m", "pip", "install", *dev_dependencies())
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
def check(args: list[str]) -> int:
    """Diagnose config, database connection and schema."""
    return py("-m", "col_pipeline.cli", "check")


@task
def run(args: list[str]) -> int:
    """Incremental load (watermark minus lookback)."""
    return py("-m", "col_pipeline.cli", "run", *args)


@task
def full_refresh(args: list[str]) -> int:
    """Re-pull the whole fuel price history."""
    return py("-m", "col_pipeline.cli", "run", "--full-refresh")


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
