"""TeeItUp / GolfNow booking engine (used by Fairfax County and many other public courses).

Endpoints (public, no login; the booking site calls them from the browser):
  GET {api}/alias/{alias}/facilities
  GET {api}/v2/tee-times?date=YYYY-MM-DD&facilityIds=1,2,3
Both require the `x-be-alias: {alias}` header. Prices are in cents; times are UTC.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..http import fetch_json, with_query
from ..models import Course, Offer, TeeTime

DEFAULT_API = "https://phx-api-be-east-1b.kenna.io"


class TeeItUp:
    def __init__(self, key: str, alias: str, fetch=None, api: str | None = None):
        self.key, self.alias = key, alias
        self.api = (api or DEFAULT_API).rstrip("/")
        self._fetch = fetch or fetch_json
        self._courses: dict[str, Course] | None = None
        self._by_course_oid: dict[str, Course] = {}

    @property
    def _headers(self) -> dict[str, str]:
        return {"x-be-alias": self.alias}

    def courses(self) -> list[Course]:
        if self._courses is None:
            raw = self._fetch(f"{self.api}/alias/{self.alias}/facilities", headers=self._headers)
            self._courses = {}
            for f in raw:
                if f.get("isSimulator"):
                    continue
                lon, lat = (f.get("location") or [None, None])[:2]
                c = Course(self.key, str(f["id"]), f["name"], f.get("timeZone") or "America/New_York", lat, lon)
                self._courses[c.id] = c
                self._by_course_oid[f["courseId"]] = c
        return list(self._courses.values())

    def booking_url(self, course: Course, day: date) -> str:
        return f"https://{self.alias}.book.teeitup.golf/?course={course.id}&date={day.isoformat()}"

    def search(self, day: date, course_ids: list[str]) -> list[TeeTime]:
        self.courses()
        url = with_query(f"{self.api}/v2/tee-times",
                         {"date": day.isoformat(), "facilityIds": ",".join(course_ids)})
        raw = self._fetch(url, headers=self._headers)
        out: list[TeeTime] = []
        wanted = set(course_ids)
        for block in raw:
            for t in block.get("teetimes", []):
                course = self._by_course_oid.get(t["courseId"])
                if course is None or course.id not in wanted:   # never trust the API to filter
                    continue
                start = datetime.fromisoformat(t["teetime"].replace("Z", "+00:00")).astimezone(
                    ZoneInfo(course.timezone))
                offers = []
                for r in t.get("rates", []):
                    if r.get("greenFeeCart") is not None:
                        offers.append(Offer(int(r.get("holes") or 18), int(r["greenFeeCart"]), True))
                    if r.get("greenFeeWalking") is not None:
                        offers.append(Offer(int(r.get("holes") or 18), int(r["greenFeeWalking"]), False))
                out.append(TeeTime(course, start, spots=int(t.get("maxPlayers") or 0),
                                   min_players=int(t.get("minPlayers") or 1), offers=offers,
                                   booking_url=self.booking_url(course, day)))
        return sorted(out, key=lambda x: (x.start, x.course.name))
