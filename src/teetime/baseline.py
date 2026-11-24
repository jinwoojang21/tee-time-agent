"""Regex parser for watch requests — the non-LLM baseline the eval compares Claude against."""

from __future__ import annotations

import re
from datetime import date, timedelta

from .rules import DAYS, WatchRule

DAY_WORDS = {d: i for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday",
                                          "saturday", "sunday"])}
COURSE_WORDS = [
    (r"twin lakes oaks|oaks course", ["7756"]),
    (r"twin lakes lakes|lakes course", ["7743"]),
    (r"twin lakes|clifton", ["7756", "7743"]),
    (r"laurel hill", ["4595"]),
    (r"burke lake", ["3485"]),
    (r"jefferson", ["17001"]),
    (r"oakmont", ["17002"]),
    (r"pinecrest", ["17005"]),
    (r"greendale", ["7742"]),
]
ALL = ["4595", "3485", "7756", "7743", "17001", "17002", "17005", "7742"]
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                          "oct", "nov", "dec"])}


def _hour(h: str, ampm: str | None, default_pm: bool = False) -> str:
    hh = int(h)
    if ampm == "pm" and hh < 12:
        hh += 12
    elif ampm is None and default_pm and hh < 8:
        hh += 12
    return f"{hh:02d}:00"


def parse(request: str, today: date, provider: str = "fairfax") -> WatchRule:
    s = request.lower()
    courses: list[str] = []
    for pat, ids in COURSE_WORDS:
        if re.search(pat, s):
            courses += [i for i in ids if i not in courses]
            s_courses = re.sub(pat, " ", s)  # avoid "twin lakes" re-matching "lakes course"
            s = s_courses if "twin lakes" in pat else s
    courses = courses or ALL

    days, dates = [], []
    m = re.search(r"(" + "|".join(MONTHS) + r")[a-z]*\.? (\d{1,2})", s)
    if m:
        dates.append(date(today.year, MONTHS[m.group(1)], int(m.group(2))).isoformat())
    if "weekend" in s:
        days = ["sat", "sun"]
    if "weekday" in s:
        days = DAYS[:5]
    for word, idx in DAY_WORDS.items():
        if re.search(rf"\b{word}s?\b", s) and not dates:
            if re.search(rf"(every {word}|{word}s\b)", s):
                days.append(DAYS[idx])
            else:
                ahead = (idx - today.weekday()) % 7 or 7
                dates.append((today + timedelta(ahead)).isoformat())
    if not days and not dates:
        days = ["sat", "sun"]

    earliest, latest = "06:00", "19:00"
    if m := re.search(r"between (\d{1,2}) and (\d{1,2}) ?(am|pm)?", s):
        earliest, latest = _hour(m.group(1), m.group(3)), _hour(m.group(2), m.group(3))
    elif m := re.search(r"(\d{1,2}) ?(am|pm)? ?(?:-|to) ?(\d{1,2}) ?(am|pm)", s):
        earliest, latest = _hour(m.group(1), m.group(2) or m.group(4)), _hour(m.group(3), m.group(4))
    else:
        if m := re.search(r"before (\d{1,2}) ?(am|pm)?", s):
            latest = _hour(m.group(1), m.group(2))
        if m := re.search(r"after (\d{1,2}) ?(am|pm)?", s):
            earliest = _hour(m.group(1), m.group(2), default_pm=True)
        if "morning" in s and "before" not in s:
            latest = "12:00"
        if "afternoon" in s:
            earliest, latest = "12:00", "17:00"
        if "twilight" in s:
            earliest = "15:00"
        if "noon" in s and "before" in s:
            latest = "12:00"

    players = 1
    if m := re.search(r"\b(?:for|of) (\d)\b|(\d) players", s):
        players = int(m.group(1) or m.group(2))
    for word, n in (("foursome", 4), ("threesome", 3), ("twosome", 2), ("two players", 2)):
        if word in s:
            players = n

    max_price = None
    if m := re.search(r"(?:under|max|less than) \$?(\d+)", s):
        max_price = float(m.group(1))

    holes = 9 if re.search(r"\b9 holes?\b|nine holes", s) else 18
    walking = "walk" if "walk" in s else "ride" if re.search(r"\b(ride|riding|cart)\b", s) else "any"
    return WatchRule(name="parsed", provider=provider, courses=courses, days=days, dates=dates,
                     earliest=earliest, latest=latest, players=players, holes=holes,
                     max_price=max_price, walking=walking)
