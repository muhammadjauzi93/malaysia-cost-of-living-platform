"""Runtime configuration, read from environment variables.

This file holds the *names* of settings and their safe defaults, never secret values.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from dotenv import load_dotenv
from sqlalchemy.engine import URL

DEFAULT_DB_HOST = "localhost"
DEFAULT_DB_PORT = 5433  # host side of the docker-compose port mapping


def db_url_from_env(env: Mapping[str, str]) -> str:
    """Resolve the database URL.

    An explicit DB_URL always wins (CI, cloud). Otherwise the URL is built from the same
    POSTGRES_* variables docker compose uses, so the password lives in exactly one place.
    """
    if explicit := env.get("DB_URL"):
        return explicit

    required = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    missing = [name for name in required if not env.get(name)]
    if missing:
        raise RuntimeError(
            f"Database settings missing: {', '.join(missing)}. "
            "Run `python tasks.py setup` to create .env, or set DB_URL directly."
        )

    # URL.create escapes special characters, so a password like "p@ss:word" still works.
    # A hand-built f-string URL would silently break on those characters.
    url = URL.create(
        drivername="postgresql+psycopg2",
        username=env["POSTGRES_USER"],
        password=env["POSTGRES_PASSWORD"],
        host=env.get("POSTGRES_HOST") or DEFAULT_DB_HOST,
        port=int(env.get("POSTGRES_PORT") or DEFAULT_DB_PORT),
        database=env["POSTGRES_DB"],
    )
    return url.render_as_string(hide_password=False)


@dataclass(frozen=True)
class Settings:
    db_url: str
    api_base_url: str = "https://api.data.gov.my"
    api_token: str | None = None
    bnm_base_url: str = "https://api.bnm.gov.my/public"
    # 1700 is the last interbank snapshot of the day, so day-over-day rates are comparable.
    # Other BNM sessions: 0900, 1130 (commercial counter rates), 1200.
    bnm_session: str = "1700"
    request_timeout_s: int = 30
    landing_dir: str = "data/landing"
    slack_webhook_url: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from os.environ (after loading .env, if present)."""
        load_dotenv()
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> Settings:
        """Build settings from any mapping. Tests and Dagster resources use this directly."""
        return cls(
            db_url=db_url_from_env(env),
            api_base_url=env.get("DATAGOVMY_BASE_URL", "https://api.data.gov.my"),
            api_token=env.get("DATAGOVMY_API_TOKEN") or None,
            bnm_base_url=env.get("BNM_BASE_URL", "https://api.bnm.gov.my/public"),
            bnm_session=env.get("BNM_SESSION", "1700"),
            request_timeout_s=int(env.get("REQUEST_TIMEOUT_S", "30")),
            landing_dir=env.get("LANDING_DIR", "data/landing"),
            slack_webhook_url=env.get("SLACK_WEBHOOK_URL") or None,
        )
