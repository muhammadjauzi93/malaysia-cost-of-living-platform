"""Create the read-only role the dashboard connects as (DECISIONS.md #58).

Run as the database owner, the same user that runs dbt: `python tasks.py create-dashboard-role`.
Safe to repeat. A repeat also resets the password to the one in .env.
"""

from __future__ import annotations

import re

from psycopg2 import sql
from sqlalchemy import Connection

from col_pipeline.dashboard.db import MART_SCHEMA

_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]*")


def grant_dashboard_reader(conn: Connection, role: str, password: str) -> None:
    """Make `role` a login that can SELECT from `marts` and do nothing else.

    Postgres roles belong to the whole server, but grants belong to one database: this grants on
    the database `conn` is connected to.

    dbt rebuilds every mart as a new table on each run, and a grant on the old table dies with it.
    The default privilege below gives SELECT on every table the current user creates in `marts`
    later, so the grant survives a rebuild. Dropping the schema itself removes that default too
    (the Dagster integration tests do this), so run this again after a schema is dropped.
    """
    if not _ROLE_NAME.fullmatch(role):
        raise ValueError(f"{role!r} is not a plain role name")
    if not password:
        raise ValueError("the dashboard role needs a password")

    role_id, schema_id = sql.Identifier(role), sql.Identifier(MART_SCHEMA)
    statements = [
        # Its password is reset on every run, so .env stays the one place it is written.
        sql.SQL(
            "DO $$ BEGIN "
            "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) THEN "
            "CREATE ROLE {role} LOGIN; END IF; END $$"
        ).format(name=sql.Literal(role), role=role_id),
        sql.SQL("ALTER ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
            role_id, sql.Literal(password)
        ),
        sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role_id),
        # dbt creates the schema on its first build. Creating it now lets the grants go in
        # before that build, so the demo path does not depend on the order of the two steps.
        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema_id),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema_id, role_id),
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema_id, role_id),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(
            schema_id, role_id
        ),
    ]
    cursor = conn.connection.cursor()
    try:
        for statement in statements:
            cursor.execute(statement)
    finally:
        cursor.close()
