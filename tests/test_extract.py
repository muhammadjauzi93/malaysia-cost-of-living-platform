import json
from datetime import date

import pytest
import requests
from tenacity import wait_none

from col_pipeline.extract import (
    BNM_ACCEPT,
    BnmClient,
    DataGovMyClient,
    JsonApiClient,
    land_raw,
    read_landed,
)


def _response(status: int, body: object) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = json.dumps(body).encode()
    resp.url = "https://api.example.test/x"
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
    monkeypatch.setattr(JsonApiClient.get_json.retry, "wait", wait_none())


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


def test_gives_up_after_four_attempts():
    session = FakeSession([_response(503, {}) for _ in range(4)])
    with pytest.raises(Exception, match="HTTP 503"):
        DataGovMyClient(session=session).get_catalogue({"id": "fuelprice"})
    assert len(session.calls) == 4


# ---- BNM ----


def test_bnm_sends_the_vendor_accept_header():
    session = FakeSession([_response(200, {"data": []})])
    BnmClient(session=session).latest_rates("1700")
    assert session.headers["Accept"] == BNM_ACCEPT == "application/vnd.BNM.API.v1+json"


def test_bnm_latest_unwraps_the_data_list(bnm_payload):
    session = FakeSession([_response(200, bnm_payload)])
    items = BnmClient(session=session).latest_rates("1700")
    assert [i["currency_code"] for i in items][:2] == ["USD", "SGD"]
    assert session.calls[0]["params"] == {"session": "1700", "quote": "rm"}
    assert session.calls[0]["url"].endswith("/exchange-rate")


def test_bnm_single_object_data_is_wrapped_in_a_list():
    item = {"currency_code": "USD", "unit": 1, "rate": {}}
    session = FakeSession([_response(200, {"data": item})])
    assert BnmClient(session=session).latest_rates("1700") == [item]


def test_bnm_unexpected_envelope_is_explicit():
    session = FakeSession([_response(200, {"message": "gone"})])
    with pytest.raises(ValueError, match="envelope"):
        BnmClient(session=session).latest_rates("1700")


def test_bnm_404_on_a_date_means_no_data_not_an_error():
    session = FakeSession([_response(404, {})])
    assert BnmClient(session=session).rates_on("USD", date(2026, 10, 24), "1700") == []
    assert len(session.calls) == 1  # and it is not retried
    assert session.calls[0]["url"].endswith("/exchange-rate/USD/date/2026-10-24")


def test_bnm_404_on_latest_is_still_an_error():
    # not_found_ok is opt-in: a 404 on the main endpoint means the URL is wrong.
    session = FakeSession([_response(404, {})])
    with pytest.raises(requests.HTTPError):
        BnmClient(session=session).latest_rates("1700")


def test_bnm_retries_transient_errors():
    session = FakeSession([_response(503, {}), _response(200, {"data": []})])
    assert BnmClient(session=session).latest_rates("1700") == []
    assert len(session.calls) == 2


# ---- landing zone ----


def test_landed_payload_round_trips(tmp_path):
    records = [{"a": 1, "nested": {"b": [1, 2]}}]
    path = land_raw(records, str(tmp_path), "demo", run_id=7)
    assert path.parent.name == "demo"
    assert path.name.endswith("_run7.json")
    assert read_landed(path) == records
