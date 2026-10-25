import pytest
from sqlalchemy.engine import make_url

from col_pipeline.config import db_url_from_env

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
