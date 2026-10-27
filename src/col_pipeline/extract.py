"""Shared extraction plumbing: HTTP clients with one retry policy, and the landing zone.

Source-specific request building lives next to each source in `col_pipeline.sources`.
Everything here returns payloads untouched. Parsing and cleaning happen in `to_frame`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = logging.getLogger(__name__)


class TransientAPIError(Exception):
    """A failure worth retrying: HTTP 429 (rate limited) or 5xx (server trouble)."""


def _is_retryable(exc: BaseException) -> bool:
    # 4xx errors other than 429 mean *our* request is wrong. Retrying won't fix it.
    return isinstance(exc, TransientAPIError | requests.ConnectionError | requests.Timeout)


class JsonApiClient:
    """Base class for JSON-over-HTTP APIs.

    One place decides what is retried (429, 5xx, network errors), always sends a timeout, and
    turns everything else into an exception. Subclasses only build URLs and parse envelopes.
    """

    def __init__(
        self,
        base_url: str,
        timeout_s: int = 30,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.session = session or requests.Session()
        if headers:
            self.session.headers.update(headers)

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        # data.gov.my allows 4 requests/minute without a token, so back off generously:
        # 15s, 30s, 60s. That is also gentle on BNM.
        wait=wait_exponential(multiplier=15, min=15, max=60),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def get_json(
        self,
        path: str,
        params: dict[str, str] | None = None,
        *,
        not_found_ok: bool = False,
    ) -> Any | None:
        """GET base_url/path and return the decoded JSON.

        With `not_found_ok=True` a 404 returns None instead of raising. Use it only where the
        API documents 404 as "no data for this request" (e.g. BNM on weekends and holidays).
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = self.session.get(url, params=params, timeout=self.timeout_s)

        if resp.status_code == 404 and not_found_ok:
            return None
        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientAPIError(f"HTTP {resp.status_code} from {resp.url}")
        resp.raise_for_status()  # any other 4xx -> HTTPError, not retried
        return resp.json()


class DataGovMyClient(JsonApiClient):
    """Client for the data.gov.my / OpenDOSM Data Catalogue API.

    API docs: https://developer.data.gov.my/static-api/data-catalogue
    """

    def __init__(
        self,
        base_url: str = "https://api.data.gov.my",
        timeout_s: int = 30,
        token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(base_url, timeout_s, session=session)
        if token:
            # Check the current auth docs on developer.data.gov.my if this ever returns 401.
            self.session.headers["Authorization"] = f"Token {token}"

    def get_catalogue(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """GET /data-catalogue with the given query params. Returns the JSON list as-is."""
        payload = self.get_json("data-catalogue", params)
        if not isinstance(payload, list):
            kind = type(payload).__name__
            raise ValueError(f"Expected a JSON list, got {kind}: {payload!r:.200}")
        return payload


BNM_ACCEPT = "application/vnd.BNM.API.v1+json"


def _unwrap_bnm(payload: Any) -> list[dict[str, Any]]:
    """BNM wraps results as {"meta": ..., "data": ...}; `data` is a list, or one object."""
    if not isinstance(payload, dict) or "data" not in payload:
        shape = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise ValueError(f"Expected a BNM envelope with a 'data' key, got: {shape}")
    data = payload["data"]
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list):
        raise ValueError(f"Expected BNM 'data' to be a list or object, got {type(data).__name__}")
    return data


class BnmClient(JsonApiClient):
    """Client for Bank Negara Malaysia's public Open API (https://api.bnm.gov.my/public).

    Needs the vendor Accept header on every request. Weekends, holidays and invalid dates
    return 404, which for date-specific lookups means "no data", not "bad request".
    """

    def __init__(
        self,
        base_url: str = "https://api.bnm.gov.my/public",
        timeout_s: int = 30,
        session: requests.Session | None = None,
    ) -> None:
        super().__init__(base_url, timeout_s, headers={"Accept": BNM_ACCEPT}, session=session)

    def latest_rates(self, session_code: str, quote: str = "rm") -> list[dict[str, Any]]:
        """Latest published rates for all currencies, quoted against ringgit by default."""
        payload = self.get_json("exchange-rate", {"session": session_code, "quote": quote})
        return _unwrap_bnm(payload)

    def rates_on(
        self, currency: str, day: date, session_code: str, quote: str = "rm"
    ) -> list[dict[str, Any]]:
        """One currency on one date. Returns [] when BNM has no data for that date."""
        payload = self.get_json(
            f"exchange-rate/{currency}/date/{day.isoformat()}",
            {"session": session_code, "quote": quote},
            not_found_ok=True,
        )
        return [] if payload is None else _unwrap_bnm(payload)


def land_raw(records: list[dict[str, Any]], landing_dir: str, dataset_id: str, run_id: int) -> Path:
    """Write the untouched payload to disk before transforming it.

    If transform has a bug, we can replay from this file instead of calling the API again.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = Path(landing_dir) / dataset_id / f"{stamp}_run{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    log.info("Landed raw payload at %s", path)
    return path


def read_landed(path: str | Path) -> list[dict[str, Any]]:
    """Read back a file written by `land_raw`."""
    return json.loads(Path(path).read_text(encoding="utf-8"))
