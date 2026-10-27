"""A tiny local HTTP server that behaves like the two real APIs, for end-to-end tests.

It speaks just enough of data.gov.my (`/data-catalogue?id=...&date_start=...@date`) and of
BNM (`/public/exchange-rate...`, vendor Accept header required) to exercise the real
`requests` code path: headers, query strings, status codes, retries. Not a mock of our code.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

BNM_ACCEPT = "application/vnd.BNM.API.v1+json"


class FakeApi:
    def __init__(
        self,
        datasets: dict[str, list[dict[str, Any]]],
        bnm_payload: dict[str, Any],
        bnm_holidays: set[date] | None = None,
    ) -> None:
        self.datasets = datasets
        self.bnm_payload = bnm_payload
        self.bnm_holidays = bnm_holidays or set()
        self.requests: list[dict[str, Any]] = []
        self.fail_next: defaultdict[str, int] = defaultdict(int)  # path -> 503s still to send
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> FakeApi:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def calls(self, prefix: str) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["path"].startswith(prefix)]

    # ---- request handling ----

    def _handler(self):
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output quiet
                pass

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # noqa: N802 - http.server naming
                parsed = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                api.requests.append(
                    {"path": parsed.path, "query": query, "accept": self.headers.get("Accept")}
                )
                if api.fail_next[parsed.path] > 0:
                    api.fail_next[parsed.path] -= 1
                    return self._send(503, {"error": "try later"})
                if parsed.path == "/data-catalogue":
                    return self._send(*api._catalogue(query))
                if parsed.path.startswith("/public/"):
                    status, body = api._bnm(parsed.path.removeprefix("/public/"), self.headers)
                    return self._send(status, body)
                self._send(404, {"error": "no such path"})

        return Handler

    def _catalogue(self, query: dict[str, str]) -> tuple[int, Any]:
        rows = self.datasets.get(query.get("id", ""))
        if rows is None:
            return 404, {"error": "unknown dataset"}
        if flt := query.get("filter"):  # "level@series_type"
            value, column = flt.split("@")
            rows = [r for r in rows if r.get(column) == value]
        if start := query.get("date_start"):  # "2025-09-11@date"
            first = start.split("@")[0]
            rows = [r for r in rows if r["date"] >= first]
        return 200, rows

    def _bnm(self, path: str, headers) -> tuple[int, Any]:
        if headers.get("Accept") != BNM_ACCEPT:
            return 406, {"error": "missing vendor Accept header"}
        parts = path.split("/")
        if parts == ["exchange-rate"]:
            return 200, self.bnm_payload
        if len(parts) == 4 and parts[0] == "exchange-rate" and parts[2] == "date":
            day = date.fromisoformat(parts[3])
            if day.weekday() >= 5 or day in self.bnm_holidays:
                return 404, {"error": "no data"}
            template = next(i for i in self.bnm_payload["data"] if i["currency_code"] == parts[1])
            item = {**template, "rate": {**template["rate"], "date": day.isoformat()}}
            return 200, {"data": item, "meta": {"total_result": 1}}
        return 404, {"error": "no such endpoint"}


def weekdays(start: date, n: int) -> list[date]:
    return [d for d in (start + timedelta(i) for i in range(n)) if d.weekday() < 5]
