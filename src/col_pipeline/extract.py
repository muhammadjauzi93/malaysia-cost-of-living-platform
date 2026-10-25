"""Extract: talk to the data.gov.my API and nothing else.

Returns the raw payload untouched. Parsing and cleaning live in transform.py.
API docs: https://developer.data.gov.my/static-api/data-catalogue
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

FUELPRICE_DATASET_ID = "fuelprice"


class TransientAPIError(Exception):
    """A failure worth retrying: HTTP 429 (rate limited) or 5xx (server trouble)."""


def _is_retryable(exc: BaseException) -> bool:
    # 4xx errors other than 429 mean *our* request is wrong. Retrying won't fix it.
    return isinstance(exc, TransientAPIError | requests.ConnectionError | requests.Timeout)


class DataGovMyClient:
    """Thin HTTP client for the data.gov.my Data Catalogue API."""

    def __init__(
        self,
        base_url: str = "https://api.data.gov.my",
        timeout_s: int = 30,
        token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.session = session or requests.Session()
        if token:
            # Check the current auth docs on developer.data.gov.my if this ever returns 401.
            self.session.headers["Authorization"] = f"Token {token}"

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(4),
        # Unauthenticated limit is 4 req/min, so back off generously: 15s, 30s, 60s.
        wait=wait_exponential(multiplier=15, min=15, max=60),
        before_sleep=before_sleep_log(log, logging.WARNING),
        reraise=True,
    )
    def get_catalogue(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """GET /data-catalogue with the given query params. Returns the JSON list as-is."""
        url = f"{self.base_url}/data-catalogue"
        resp = self.session.get(url, params=params, timeout=self.timeout_s)

        if resp.status_code == 429 or resp.status_code >= 500:
            raise TransientAPIError(f"HTTP {resp.status_code} from {resp.url}")
        resp.raise_for_status()  # any other 4xx -> HTTPError, not retried

        payload = resp.json()
        if not isinstance(payload, list):
            kind = type(payload).__name__
            raise ValueError(f"Expected a JSON list, got {kind}: {payload!r:.200}")
        return payload


def fuel_price_params(since: date | None = None) -> dict[str, str]:
    """Query params for the fuelprice dataset. Pure function, so it is trivially testable."""
    params = {
        "id": FUELPRICE_DATASET_ID,
        "filter": "level@series_type",  # prices only; weekly changes are derivable later
        "sort": "date",
    }
    if since is not None:
        params["date_start"] = f"{since.isoformat()}@date"
    return params


def fetch_fuel_prices(client: DataGovMyClient, since: date | None = None) -> list[dict[str, Any]]:
    params = fuel_price_params(since)
    log.info("Requesting dataset=%s since=%s", FUELPRICE_DATASET_ID, since or "beginning")
    records = client.get_catalogue(params)
    log.info("Received %d records", len(records))
    return records


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
