from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Course:
    provider: str          # key from teewatch.yaml, e.g. "fairfax"
    id: str                # provider's course/facility id
    name: str
    timezone: str = "America/New_York"
    lat: float | None = None
    lon: float | None = None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.id}"


@dataclass(frozen=True)
class Offer:
    holes: int
    price_cents: int | None
    cart: bool | None       # True = cart included, False = walking rate, None = unknown


@dataclass
class TeeTime:
    course: Course
    start: datetime          # timezone-aware, course-local
    spots: int               # how many players can still book this slot
    min_players: int = 1
    offers: list[Offer] = field(default_factory=list)
    booking_url: str | None = None

    def best_price(self, holes: int | None = None, walking: str = "any") -> int | None:
        """Cheapest per-player price (cents) for the requested holes / walking preference."""
        prices = [
            o.price_cents for o in self.offers
            if o.price_cents is not None
            and (holes is None or o.holes == holes)
            and (walking == "any" or (walking == "walk" and o.cart is False) or (walking == "ride" and o.cart))
        ]
        return min(prices) if prices else None

    def has_holes(self, holes: int | None) -> bool:
        return holes is None or not self.offers or any(o.holes == holes for o in self.offers)

    @property
    def key(self) -> str:
        return f"{self.course.key}@{self.start.isoformat()}"
