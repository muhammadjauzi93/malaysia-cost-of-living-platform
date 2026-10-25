import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fuel_records() -> list[dict]:
    """Same shape as the real API response. The first two rows are real; the rest are
    illustrative values chosen to exercise edge cases (change rows, nulls, new columns)."""
    return json.loads((FIXTURES / "fuelprice_sample.json").read_text())
