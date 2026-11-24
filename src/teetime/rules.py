"""Watch rules — what tee times you want. Stored in teewatch.yaml; matched deterministically."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class WatchRule:
    name: str
    provider: str
    courses: list[str]                         # provider course ids
    days: list[str] = field(default_factory=list)    # recurring weekdays, e.g. ["sat", "sun"]
    dates: list[str] = field(default_factory=list)   # explicit ISO dates
    horizon_days: int = 7                      # how far ahead recurring days are checked
    earliest: str = "00:00"                    # local time, inclusive
    latest: str = "23:59"                      # local time, inclusive
    players: int = 1
    holes: int | None = 18                     # 9, 18 or None for either
    max_price: float | None = None             # dollars per player
    walking: str = "any"                       # any | walk | ride
    max_rain_chance: int | None = None         # skip slots whose forecast rain chance is higher (%)

    # ------------------------------------------------------------------ validation
    def validate(self) -> WatchRule:
        errors = []
        if not self.courses:
            errors.append("courses must not be empty")
        bad = [d for d in self.days if d not in DAYS]
        if bad:
            errors.append(f"days must be in {DAYS}, got {bad}")
        for d in self.dates:
            try:
                date.fromisoformat(d)
            except ValueError:
                errors.append(f"date {d!r} is not YYYY-MM-DD")
        if not self.days and not self.dates:
            errors.append("give days (recurring) or dates (specific)")
        try:
            if _t(self.earliest) > _t(self.latest):
                errors.append("earliest is after latest")
        except ValueError:
            errors.append("earliest/latest must be HH:MM (24h)")
        if not 1 <= self.players <= 4:
            errors.append("players must be 1-4")
        if self.holes not in (9, 18, None):
            errors.append("holes must be 9, 18 or null")
        if self.walking not in ("any", "walk", "ride"):
            errors.append("walking must be any|walk|ride")
        if self.max_price is not None and self.max_price <= 0:
            errors.append("max_price must be positive")
        if not 1 <= self.horizon_days <= 30:
            errors.append("horizon_days must be 1-30")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WatchRule:
        known = {f for f in cls.__dataclass_fields__}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown watch fields: {sorted(unknown)}")
        d = dict(d)
        d["courses"] = [str(c) for c in d.get("courses", [])]
        d["days"] = [str(x).lower()[:3] for x in d.get("days", [])]
        d["dates"] = [str(x) for x in d.get("dates", [])]
        return cls(**d).validate()

    def to_dict(self) -> dict[str, Any]:
        defaults = WatchRule(self.name, self.provider, self.courses)
        return {k: v for k, v in asdict(self).items()
                if k in ("name", "provider", "courses") or v != getattr(defaults, k)}

    # ------------------------------------------------------------------ matching
    def target_dates(self, today: date) -> list[date]:
        out = {date.fromisoformat(d) for d in self.dates if date.fromisoformat(d) >= today}
        if self.days:
            for i in range(self.horizon_days + 1):
                d = today + timedelta(days=i)
                if DAYS[d.weekday()] in self.days:
                    out.add(d)
        return sorted(out)

    def matches(self, tt, now: datetime | None = None, rain_chance: int | None = None) -> bool:
        local = tt.start
        if now is not None and local <= now:
            return False
        if not (_t(self.earliest) <= local.time().replace(second=0, microsecond=0) <= _t(self.latest)):
            return False
        if tt.spots < self.players or tt.min_players > self.players:
            return False
        if not tt.has_holes(self.holes):
            return False
        if self.max_price is not None:
            price = tt.best_price(self.holes, self.walking)
            if price is None or price > self.max_price * 100:
                return False
        elif self.walking != "any" and tt.offers and tt.best_price(self.holes, self.walking) is None:
            return False
        if self.max_rain_chance is not None and rain_chance is not None and rain_chance > self.max_rain_chance:
            return False
        return True


def _t(s: str) -> time:
    return time.fromisoformat(s)
