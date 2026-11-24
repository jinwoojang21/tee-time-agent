"""Tee-sheet providers. Each turns a (date, courses) query into normalised TeeTime objects."""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from ..models import Course, TeeTime


class ProviderNotConfigured(RuntimeError):
    """Raised when a provider needs something the user hasn't supplied (e.g. a session cookie)."""


class Provider(Protocol):
    key: str

    def courses(self) -> list[Course]: ...

    def search(self, day: date, course_ids: list[str]) -> list[TeeTime]: ...


def build_provider(key: str, spec: dict[str, Any], fetch=None) -> Provider:
    kind = spec.get("kind")
    if kind == "teeitup":
        from .teeitup import TeeItUp

        return TeeItUp(key, alias=spec["alias"], fetch=fetch, api=spec.get("api"))
    if kind == "json":
        from .json_endpoint import JsonEndpoint

        return JsonEndpoint(key, spec, fetch=fetch)
    raise ValueError(f"provider {key!r}: unknown kind {kind!r} (expected teeitup|json)")
