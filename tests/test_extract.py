import json
from datetime import date

import pytest
import requests
from tenacity import wait_none

from col_pipeline.extract import DataGovMyClient, fuel_price_params


def _response(status: int, body: object) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode()
    resp.url = "https://api.data.gov.my/data-catalogue"
    resp.reason = "test"
    return resp


class FakeSession:
    """Stands in for requests.Session and replays a scripted list of responses."""

    def __init__(self, responses: list[requests.Response]) -> None:
        self.responses = responses
        self.calls: list[dict] = []
        self.headers: dict[str, str] = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    # Keep the retry policy, skip the real 15-60s waits.
    monkeypatch.setattr(DataGovMyClient.get_catalogue.retry, "wait", wait_none())


def test_full_history_params():
    assert fuel_price_params() == {"id": "fuelprice", "filter": "level@series_type", "sort": "date"}


def test_incremental_params_add_date_start():
    assert fuel_price_params(date(2026, 9, 1))["date_start"] == "2026-09-01@date"


def test_always_sends_a_timeout():
    session = FakeSession([_response(200, [])])
    DataGovMyClient(session=session, timeout_s=30).get_catalogue({"id": "fuelprice"})
    assert session.calls[0]["timeout"] == 30


def test_retries_on_503_then_succeeds():
    session = FakeSession([_response(503, {}), _response(429, {}), _response(200, [{"a": 1}])])
    out = DataGovMyClient(session=session).get_catalogue({"id": "fuelprice"})
    assert out == [{"a": 1}]
    assert len(session.calls) == 3


def test_does_not_retry_a_client_error():
    session = FakeSession([_response(404, {"error": "unknown id"})])
    with pytest.raises(requests.HTTPError):
        DataGovMyClient(session=session).get_catalogue({"id": "typo"})
    assert len(session.calls) == 1


def test_rejects_non_list_payload():
    session = FakeSession([_response(200, {"status": "maintenance"})])
    with pytest.raises(ValueError, match="Expected a JSON list"):
        DataGovMyClient(session=session).get_catalogue({"id": "fuelprice"})
