"""Database access for the dashboard. Every read goes through here, and only `marts` is reachable.

Two layers keep the dashboard on the marts (DECISIONS.md #58). This module is the first: callers
pass a bare table name, never SQL and never a schema, and the name must look like a plain
identifier. The second is the database itself: the app connects as `dashboard_reader`, which can
SELECT from `marts` and nothing else, so even a bug here cannot read `raw` or write anything.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine, func, select, table, text
from sqlalchemy.sql.expression import TableClause

from col_pipeline.config import dashboard_db_url_from_env

MART_SCHEMA = "marts"
# Lower-case letters, digits and underscores, as dbt names every model. No dots, so a caller
# cannot name another schema, and no quotes or spaces, so a name can never carry SQL.
_TABLE_NAME = re.compile(r"[a-z_][a-z0-9_]*")


class NotAMartError(ValueError):
    """Raised for a table name that is not a plain name inside `marts`."""


def mart_table(name: str) -> TableClause:
    """The table `marts.<name>`, after checking that `name` cannot point anywhere else."""
    if not isinstance(name, str) or not _TABLE_NAME.fullmatch(name):
        raise NotAMartError(
            f"{name!r} is not a mart table name. The dashboard reads only the {MART_SCHEMA} "
            "schema: pass a bare model name such as 'fct_fuel_vs_transport_cpi'."
        )
    return table(name, schema=MART_SCHEMA)


def make_engine(env: Mapping[str, str] | None = None) -> Engine:
    """An engine that connects as the read-only dashboard role.

    Every session is also opened read-only from this side, so a write fails even against a
    database whose role was set up by hand without that setting.
    """
    if env is None:
        load_dotenv()
        env = os.environ
    return create_engine(
        dashboard_db_url_from_env(env),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c default_transaction_read_only=on"},
    )


def row_count(engine: Engine, name: str) -> int:
    """Number of rows in `marts.<name>`."""
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(mart_table(name))).scalar_one()


def read_mart(engine: Engine, name: str) -> pd.DataFrame:
    """Every row of `marts.<name>` as a DataFrame."""
    with engine.connect() as conn:
        return pd.read_sql(select(text("*")).select_from(mart_table(name)), conn)
