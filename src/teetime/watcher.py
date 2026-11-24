"""The scheduled check: no LLM, one request per (provider, date), alert only on NEW matches."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Config
from .notify import Match
from .providers import ProviderNotConfigured
from .weather import Weather

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    new: list[Match] = field(default_factory=list)
    already_seen: int = 0
    requests: int = 0
    errors: list[str] = field(default_factory=list)


class State:
    """Slots we've already alerted on, so each tee time pings you once."""

    def __init__(self, path: Path):
        self.path = path
        self.seen: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}

    def prune(self, now: datetime) -> None:
        # keys end with the slot's ISO start time; drop slots in the past
        self.seen = {k: v for k, v in self.seen.items()
                     if datetime.fromisoformat(k.split("@", 1)[1].split("#")[0]) > now}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.seen, indent=1, sort_keys=True))


def run(config: Config, state: State, now: datetime | None = None, fetch=None, weather: Weather | None = None,
        only: list[str] | None = None) -> RunResult:
    tz = ZoneInfo(config.timezone)
    now = now or datetime.now(tz)
    providers = config.providers(fetch=fetch)
    weather = weather or Weather(fetch=fetch)
    state.prune(now)
    result = RunResult()
    cache: dict[tuple, list] = {}

    for w in config.watches:
        if only and w.name not in only:
            continue
        prov = providers[w.provider]
        for day in w.target_dates(now.date()):
            key = (w.provider, day, tuple(sorted(w.courses)))
            if key not in cache:
                try:
                    cache[key] = prov.search(day, sorted(w.courses))
                    result.requests += 1
                except ProviderNotConfigured as e:
                    result.errors.append(f"{w.provider}: {e}")
                    cache[key] = []
                except Exception as e:  # noqa: BLE001 — one bad day shouldn't kill the run
                    result.errors.append(f"{w.provider} {day}: {type(e).__name__}: {e}")
                    cache[key] = []
            for tt in cache[key]:
                fc = weather.at(tt.course.lat, tt.course.lon, tt.start)  # cached per course/day
                if not w.matches(tt, now=now, rain_chance=fc.rain_chance if fc else None):
                    continue
                skey = f"{tt.key}#{w.name}"
                if skey in state.seen:
                    result.already_seen += 1
                    continue
                state.seen[skey] = now.isoformat()
                result.new.append(Match(w.name, tt, tt.best_price(w.holes, w.walking), fc))
    result.new.sort(key=lambda m: (m.teetime.start, m.teetime.course.name))
    return result
