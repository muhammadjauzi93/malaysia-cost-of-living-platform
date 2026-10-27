"""Dagster resources: where connection details come from.

Credentials are never written in code or in Dagster config. The resource reads the same
environment variables (and .env file) as the CLI, so there is one place to configure them.
Both fields are optional overrides, used by tests to point at a throwaway database.
"""

from __future__ import annotations

import os

import dagster as dg
from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

from col_pipeline.config import Settings


class PipelineResource(dg.ConfigurableResource):
    db_url: str | None = None
    landing_dir: str | None = None

    def settings(self) -> Settings:
        load_dotenv()
        env = dict(os.environ)
        if self.db_url:
            env["DB_URL"] = self.db_url
        if self.landing_dir:
            env["LANDING_DIR"] = self.landing_dir
        return Settings.from_mapping(env)

    def engine(self, settings: Settings | None = None) -> Engine:
        return create_engine((settings or self.settings()).db_url, pool_pre_ping=True)
