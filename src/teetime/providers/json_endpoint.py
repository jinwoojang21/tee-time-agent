"""A provider for any tee sheet that a logged-in page loads as JSON — configured, not coded.

This is how a private-club portal (e.g. Piedmont Club on the Invited member site) plugs in:
open the tee sheet while signed in, find the JSON request in DevTools → Network, and describe
it in YAML (see providers/piedmont.example.yaml). Secrets such as the session cookie come from
environment variables via ${VAR}; they are never written to the config file.

Field paths are dotted (`data.items.0.time`), `*` flattens a list (`*.teetimes`), and
`a|b` takes the first path that has a value.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..http import fetch_json
from ..models import Course, Offer, TeeTime
from . import ProviderNotConfigured

_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _env(s: str) -> str:
    def sub(m: re.Match) -> str:
        val = os.getenv(m.group(1))
        if not val:
            raise ProviderNotConfigured(f"environment variable {m.group(1)} is not set")
        return val
    return _ENV.sub(sub, s)


def _fill(template: str, values: dict[str, str]) -> str:
    """Replace only {date}, {date_us}, {course_id} — leaves ${SECRETS} and other braces alone."""
    for k, v in values.items():
        template = template.replace("{" + k + "}", v)
    return template


def get_path(obj: Any, path: str | None) -> Any:
    if path is None:
        return None
    for alt in path.split("|"):
        cur: Any = obj
        for part in alt.split("."):
            if cur is None:
                break
            if isinstance(cur, list):
                cur = cur[int(part)] if part.isdigit() and int(part) < len(cur) else None
            elif isinstance(cur, dict):
                cur = cur.get(part)
            else:
                cur = None
        if cur is not None:
            return cur
    return None


def select_items(obj: Any, path: str) -> list[Any]:
    """`*.teetimes` → concatenate obj[i]['teetimes'] for every i."""
    items = [obj]
    for part in path.split("."):
        nxt: list[Any] = []
        for it in items:
            if part == "*":
                nxt.extend(it if isinstance(it, list) else [])
            else:
                v = get_path(it, part)
                if v is not None:
                    nxt.append(v)
        items = nxt
    flat: list[Any] = []
    for it in items:
        flat.extend(it if isinstance(it, list) else [it])
    return flat


class JsonEndpoint:
    def __init__(self, key: str, spec: dict[str, Any], fetch=None):
        self.key, self.spec = key, spec
        self._fetch = fetch or fetch_json
        self.tz = spec.get("timezone", "America/New_York")
        self._courses = {
            str(c["id"]): Course(key, str(c["id"]), c["name"], self.tz, c.get("lat"), c.get("lon"))
            for c in spec.get("courses", [])
        }
        if not self._courses:
            raise ValueError(f"provider {key!r}: list at least one course")

    def courses(self) -> list[Course]:
        return list(self._courses.values())

    def search(self, day: date, course_ids: list[str]) -> list[TeeTime]:
        req, resp = self.spec["request"], self.spec["response"]
        out: list[TeeTime] = []
        for cid in course_ids:
            course = self._courses[cid]
            fmt = {"date": day.isoformat(), "date_us": day.strftime("%m/%d/%Y"), "course_id": cid}
            url = _env(_fill(req["url"], fmt))
            headers = {k: _env(_fill(str(v), fmt)) for k, v in (req.get("headers") or {}).items()}
            raw = self._fetch(url, headers=headers)
            for item in select_items(raw, resp["items"]):
                start_raw = get_path(item, resp["start"])
                if start_raw is None:
                    continue
                start = self._parse_start(str(start_raw), day)
                price = get_path(item, resp.get("price_cents"))
                if price is None and resp.get("price_dollars"):
                    p = get_path(item, resp["price_dollars"])
                    price = round(float(p) * 100) if p is not None else None
                holes = get_path(item, resp.get("holes")) or resp.get("default_holes", 18)
                spots = get_path(item, resp.get("spots"))
                out.append(TeeTime(
                    course, start,
                    spots=int(spots) if spots is not None else int(resp.get("default_spots", 4)),
                    offers=[Offer(int(holes), int(price) if price is not None else None, None)],
                    booking_url=_fill(self.spec.get("booking_url") or "", fmt) or None,
                ))
        return sorted(out, key=lambda t: t.start)

    def _parse_start(self, s: str, day: date) -> datetime:
        tz = ZoneInfo(self.tz)
        fmt = self.spec["response"].get("start_format")
        if fmt:
            dt = datetime.strptime(s, fmt)
            if fmt in ("%H:%M", "%I:%M %p", "%I:%M%p"):
                dt = datetime.combine(day, dt.time())
            return dt.replace(tzinfo=tz)
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
