"""Phone alerts via ntfy (https://ntfy.sh): free, no account, instant push.

Each alert carries "Book" action buttons that open the course's booking page on that date.
Tapping one *is* the approval step: nothing is booked until you complete it yourself.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .http import post
from .models import TeeTime
from .weather import HourForecast


@dataclass
class Match:
    watch: str
    teetime: TeeTime
    price_cents: int | None
    forecast: HourForecast | None = None

    def line(self) -> str:
        t = self.teetime
        price = f"${self.price_cents / 100:.0f}" if self.price_cents is not None else "price n/a"
        wx = f" · {self.forecast.short()}" if self.forecast and self.forecast.short() else ""
        return (f"{t.start:%a %b %-d %-I:%M %p} · {t.course.name} · {t.spots} spot"
                f"{'s' if t.spots != 1 else ''} · {price}{wx}")


def format_alert(matches: list[Match]) -> tuple[str, str, list[tuple[str, str]]]:
    """Return (title, body, [(button label, url)]) — at most 3 buttons (ntfy limit)."""
    watches = sorted({m.watch for m in matches})
    title = f"⛳ {len(matches)} new tee time{'s' if len(matches) != 1 else ''} · {', '.join(watches)}"
    body = "\n".join(m.line() for m in matches[:12])
    if len(matches) > 12:
        body += f"\n…and {len(matches) - 12} more"
    buttons, seen = [], set()
    for m in matches:
        t = m.teetime
        if t.booking_url and t.booking_url not in seen:
            seen.add(t.booking_url)
            short = t.course.name.replace(" Golf Course", "").replace(" Golf Club", "")
            buttons.append((f"Book {t.start:%-I:%M %p} {short}"[:40], t.booking_url))
        if len(buttons) == 3:
            break
    return title, body, buttons


def ntfy_payload(matches: list[Match], topic: str) -> dict:
    title, body, buttons = format_alert(matches)
    payload = {"topic": topic, "title": title, "message": body, "tags": ["golf"], "priority": 4}
    if buttons:
        payload["click"] = buttons[0][1]
        payload["actions"] = [{"action": "view", "label": label, "url": url} for label, url in buttons]
    return payload


def send_ntfy(matches: list[Match], topic: str | None = None, server: str | None = None, sender=post) -> bool:
    """Publish as JSON (keeps emoji/UTF-8 intact, unlike HTTP header publishing)."""
    topic = topic or os.getenv("NTFY_TOPIC")
    if not topic:
        return False
    server = (server or os.getenv("NTFY_SERVER") or "https://ntfy.sh").rstrip("/")
    sender(server, json.dumps(ntfy_payload(matches, topic)), {"Content-Type": "application/json"})
    return True
