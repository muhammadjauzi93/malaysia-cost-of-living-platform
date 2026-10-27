import json
from pathlib import Path

import pytest

from col_pipeline.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def fuel_records() -> list[dict]:
    """Same shape as the real API response. The first two rows are real; the rest are
    illustrative values chosen to exercise edge cases (change rows, nulls, new columns)."""
    return load_fixture("fuelprice_sample.json")


@pytest.fixture
def cpi_records() -> list[dict]:
    """Shape from OpenDOSM's published variable definitions; values are illustrative."""
    return load_fixture("cpi_state_sample.json")


@pytest.fixture
def bnm_payload() -> dict:
    """Envelope and item shape follow BNM's JSON; the rate values are illustrative."""
    return load_fixture("bnm_exchange_rate_sample.json")


@pytest.fixture
def fx_records(bnm_payload) -> list[dict]:
    """What `fx_rates.fetch` hands to `to_frame`: BNM items plus the request's session."""
    return [{**item, "_session": "1700"} for item in bnm_payload["data"]]


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings that point nowhere real. Anything that tries the network will fail loudly."""
    return Settings(
        db_url="postgresql+psycopg2://nobody:nothing@localhost:1/none",
        landing_dir=str(tmp_path / "landing"),
    )
