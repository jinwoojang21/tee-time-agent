"""Tiny HTTP layer (stdlib only). Injectable so tests never touch the network."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, Protocol

USER_AGENT = "tee-time-agent/0.1 (+https://github.com/jinwoojang21/tee-time-agent)"


class Fetch(Protocol):
    def __call__(self, url: str, headers: dict[str, str] | None = None) -> Any: ...


def fetch_json(url: str, headers: dict[str, str] | None = None, timeout: float = 20) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json",
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — URLs come from config
        return json.loads(resp.read().decode())


def post(url: str, body: str, headers: dict[str, str] | None = None, timeout: float = 20) -> int:
    req = urllib.request.Request(url, data=body.encode(), method="POST",
                                 headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.status


def with_query(url: str, params: dict[str, Any]) -> str:
    return f"{url}?{urllib.parse.urlencode(params)}"


FetchFn = Callable[..., Any]
