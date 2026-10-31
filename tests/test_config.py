import pytest
from sqlalchemy.engine import make_url

from col_pipeline.config import dashboard_db_url_from_env, db_url_from_env

PARTS = {"POSTGRES_USER": "col_user", "POSTGRES_PASSWORD": "secret", "POSTGRES_DB": "col_platform"}


def test_builds_url_from_postgres_variables_with_default_port():
    url = make_url(db_url_from_env(PARTS))
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "col_user",
        "secret",
        "localhost",
        5433,
        "col_platform",
    )
    assert url.drivername == "postgresql+psycopg2"


def test_port_and_host_can_be_overridden():
    url = make_url(db_url_from_env({**PARTS, "POSTGRES_PORT": "6543", "POSTGRES_HOST": "db"}))
    assert (url.host, url.port) == ("db", 6543)


def test_special_characters_in_password_survive():
    # '@' and ':' would corrupt a hand-built f-string URL.
    url = make_url(db_url_from_env({**PARTS, "POSTGRES_PASSWORD": "p@ss:w/rd#1"}))
    assert url.password == "p@ss:w/rd#1"
    assert url.host == "localhost"


def test_explicit_db_url_wins():
    explicit = "postgresql+psycopg2://ci:ci@localhost:5432/col_ci"
    assert db_url_from_env({**PARTS, "DB_URL": explicit}) == explicit


def test_missing_settings_name_what_is_missing():
    with pytest.raises(RuntimeError, match="POSTGRES_PASSWORD"):
        db_url_from_env({"POSTGRES_USER": "u", "POSTGRES_DB": "d"})


DASHBOARD = {**PARTS, "DASHBOARD_DB_PASSWORD": "reader-secret"}


def test_dashboard_url_is_the_same_database_as_the_reader_role():
    url = make_url(dashboard_db_url_from_env(DASHBOARD))
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "dashboard_reader",
        "reader-secret",
        "localhost",
        5433,
        "col_platform",
    )


def test_dashboard_url_follows_db_url_but_swaps_the_user():
    # CI sets only DB_URL; the dashboard must still reach that database, as the reader.
    env = {"DB_URL": "postgresql+psycopg2://ci:ci@db:5432/col_ci", "DASHBOARD_DB_PASSWORD": "r"}
    url = make_url(dashboard_db_url_from_env(env))
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "dashboard_reader",
        "r",
        "db",
        5432,
        "col_ci",
    )


def test_dashboard_password_with_special_characters_survives():
    url = make_url(dashboard_db_url_from_env({**PARTS, "DASHBOARD_DB_PASSWORD": "p@ss:w/rd#1"}))
    assert url.password == "p@ss:w/rd#1"


def test_dashboard_user_can_be_renamed():
    url = make_url(dashboard_db_url_from_env({**DASHBOARD, "DASHBOARD_DB_USER": "viewer"}))
    assert url.username == "viewer"


def test_explicit_dashboard_url_wins():
    explicit = "postgresql+psycopg2://viewer:v@host:5432/other"
    assert dashboard_db_url_from_env({**DASHBOARD, "DASHBOARD_DB_URL": explicit}) == explicit


def test_dashboard_never_falls_back_to_the_pipeline_password():
    # Connecting as the owner would silently bypass the read-only role, so a missing reader
    # password is an error, not a reason to reuse POSTGRES_PASSWORD.
    with pytest.raises(RuntimeError, match="DASHBOARD_DB_PASSWORD"):
        dashboard_db_url_from_env(PARTS)
