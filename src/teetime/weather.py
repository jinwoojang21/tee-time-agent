"""Hourly forecast from Open-Meteo (free, no API key). Failures degrade to "no forecast"."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .http import fetch_json, with_query

API = "https://api.open-meteo.com/v1/forecast"


@dataclass
class HourForecast:
    temp_f: float | None
    rain_chance: int | None
    wind_mph: float | None

    def short(self) -> str:
        parts = []
        if self.temp_f is not None:
            parts.append(f"{self.temp_f:.0f}°F")
        if self.rain_chance is not None:
            parts.append(f"{self.rain_chance}% rain")
        if self.wind_mph is not None and self.wind_mph >= 12:
            parts.append(f"wind {self.wind_mph:.0f} mph")
        return ", ".join(parts)


class Weather:
    def __init__(self, fetch=None):
        self._fetch = fetch or fetch_json
        self._cache: dict[tuple, dict[str, HourForecast]] = {}

    def at(self, lat: float | None, lon: float | None, when: datetime) -> HourForecast | None:
        if lat is None or lon is None:
            return None
        day: date = when.date()
        key = (round(lat, 2), round(lon, 2), day)
        if key not in self._cache:
            try:
                raw = self._fetch(with_query(API, {
                    "latitude": lat, "longitude": lon, "start_date": day.isoformat(), "end_date": day.isoformat(),
                    "hourly": "temperature_2m,precipitation_probability,wind_speed_10m",
                    "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                    "timezone": str(when.tzinfo) if when.tzinfo else "auto",
                }))
                h = raw["hourly"]
                self._cache[key] = {
                    t[11:13]: HourForecast(h["temperature_2m"][i], h["precipitation_probability"][i],
                                           h["wind_speed_10m"][i])
                    for i, t in enumerate(h["time"])
                }
            except Exception:  # noqa: BLE001 — weather is a nice-to-have
                self._cache[key] = {}
        return self._cache[key].get(f"{when.hour:02d}")
