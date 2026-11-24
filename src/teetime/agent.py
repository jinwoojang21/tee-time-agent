"""Claude agents: turn plain English into a watch rule, and answer tee-time questions.

`add`  — Claude resolves dates with a calendar tool, looks up course ids, and proposes a WatchRule.
         The rule is validated by code and shown to you; nothing is saved until you confirm.
`ask`  — Claude searches live tee sheets (+ forecast) and answers with booking links.
The scheduled watcher never calls the model, so monitoring costs nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .config import Config
from .rules import DAYS, WatchRule
from .weather import Weather

RULE_PROPS = {
    "name": {"type": "string", "description": "short kebab-case name, e.g. 'sat-early-foursome'"},
    "provider": {"type": "string"},
    "courses": {"type": "array", "items": {"type": "string"}, "description": "course ids from list_courses"},
    "days": {"type": "array", "items": {"type": "string", "enum": DAYS},
             "description": "RECURRING weekdays (e.g. 'every Saturday'). Leave empty for specific dates."},
    "dates": {"type": "array", "items": {"type": "string"},
              "description": "SPECIFIC dates YYYY-MM-DD (e.g. 'this Saturday', 'Thanksgiving')."},
    "horizon_days": {"type": "integer", "minimum": 1, "maximum": 30,
                     "description": "for recurring days: how many days ahead to watch (default 7)"},
    "earliest": {"type": "string", "description": "HH:MM 24h, inclusive"},
    "latest": {"type": "string", "description": "HH:MM 24h, inclusive"},
    "players": {"type": "integer", "minimum": 1, "maximum": 4},
    "holes": {"type": ["integer", "null"], "enum": [9, 18, None]},
    "max_price": {"type": ["number", "null"], "description": "dollars per player"},
    "walking": {"type": "string", "enum": ["any", "walk", "ride"]},
    "max_rain_chance": {"type": ["integer", "null"], "description": "percent; only if the user mentions weather"},
}

COMMON_TOOLS = [
    {"name": "calendar", "description": "List dates with weekday names, to resolve 'this weekend', "
                                        "'next Friday', holidays etc. without arithmetic mistakes.",
     "input_schema": {"type": "object", "properties": {
         "start": {"type": "string", "description": "YYYY-MM-DD; default today"},
         "days": {"type": "integer", "minimum": 1, "maximum": 45, "default": 14}}}},
    {"name": "list_courses", "description": "Courses per provider with ids, names and town.",
     "input_schema": {"type": "object", "properties": {}}},
]
ADD_TOOLS = COMMON_TOOLS + [
    {"name": "propose_watch", "description": "Propose the watch rule. It is validated; fix any error returned.",
     "input_schema": {"type": "object", "properties": RULE_PROPS,
                      "required": ["name", "provider", "courses", "players"]}},
]
ASK_TOOLS = COMMON_TOOLS + [
    {"name": "search_tee_times", "description": "Live tee times for one date across courses, with forecast.",
     "input_schema": {"type": "object", "properties": {
         "provider": {"type": "string"}, "date": {"type": "string"},
         "courses": {"type": "array", "items": {"type": "string"}},
         "players": {"type": "integer", "default": 1}},
         "required": ["provider", "date", "courses"]}},
    {"name": "answer", "description": "Final answer for the user, with booking links.",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
]

ADD_SYSTEM = """\
You turn a golfer's request into a tee-time watch rule. Today is {today} ({weekday}), timezone {tz}.
Rules:
- Use the calendar tool to resolve any relative date. "This weekend" / "Saturday" / "Thanksgiving"
  are SPECIFIC dates; "every Saturday" / "weekends" / "Saturdays" are RECURRING days.
- Use list_courses and pick course ids that match what the user named (a place name like "Clifton"
  means every course there). If no course is named, include all courses of the provider.
- Times are 24h HH:MM, inclusive. House conventions: "morning" 06:00-12:00, "early morning" 06:00-09:00,
  "afternoon" 12:00-17:00, "twilight" 15:00-18:00; "before N" sets only latest, "after N" only earliest.
- Players: count the user plus everyone they mention (foursome = 4, threesome = 3); default 1.
- Holes: default 18 unless 9 is mentioned. walking: "walk"/"ride" only if stated, else "any".
- Only set max_price / max_rain_chance if the user gave a limit.
Call propose_watch once you're confident; if it returns an error, fix and call again.
"""

ASK_SYSTEM = """\
You help a golfer find tee times. Today is {today} ({weekday}), timezone {tz}. Resolve dates with the
calendar tool, find course ids with list_courses, search live tee sheets, and answer concisely:
best options first (time, course, spots, price, forecast) with each booking link. Never invent times.
You cannot book; the user books by opening the link.
"""


@dataclass
class AgentRun:
    rule: WatchRule | None = None
    answer: str | None = None
    tool_calls: list[tuple[str, dict, bool]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "turns": 0})


class TeeAgent:
    def __init__(self, config: Config, today: date, model: str, client: Any | None = None, fetch=None,
                 max_turns: int = 10):
        self.cfg, self.today, self.model, self.max_turns = config, today, model, max_turns
        self.client, self.fetch = client, fetch
        self._providers = None

    @property
    def providers(self):
        if self._providers is None:
            self._providers = self.cfg.providers(fetch=self.fetch)
        return self._providers

    # ---------------------------------------------------------------- tools
    def t_calendar(self, run: AgentRun, start: str | None = None, days: int = 14) -> Any:
        s = date.fromisoformat(start) if start else self.today
        return [f"{(s + timedelta(i)).isoformat()} {DAYS[(s + timedelta(i)).weekday()]}"
                for i in range(min(int(days), 45))]

    def t_list_courses(self, run: AgentRun) -> Any:
        out = {}
        for key, p in self.providers.items():
            try:
                out[key] = [{"id": c.id, "name": c.name} for c in p.courses()]
            except Exception as e:  # noqa: BLE001
                out[key] = f"unavailable: {e}"
        return out

    def t_propose_watch(self, run: AgentRun, **kw: Any) -> Any:
        kw = {k: v for k, v in kw.items() if v is not None or k in ("holes", "max_price", "max_rain_chance")}
        rule = WatchRule.from_dict(kw)
        if rule.provider not in self.cfg.provider_specs:
            raise ValueError(f"unknown provider {rule.provider!r}")
        run.rule = rule
        return {"ok": True, "next_dates": [d.isoformat() for d in rule.target_dates(self.today)][:6]}

    def t_search_tee_times(self, run: AgentRun, provider: str, date: str, courses: list[str],
                           players: int = 1) -> Any:
        from datetime import date as _date

        tts = self.providers[provider].search(_date.fromisoformat(date), [str(c) for c in courses])
        wx = Weather(fetch=self.fetch)
        rows = []
        for t in tts:
            if t.spots < players:
                continue
            fc = wx.at(t.course.lat, t.course.lon, t.start)
            rows.append({"time": t.start.strftime("%a %-I:%M %p"), "course": t.course.name, "spots": t.spots,
                         "from_price": (t.best_price() or 0) / 100 or None,
                         "holes": sorted({o.holes for o in t.offers}), "forecast": fc.short() if fc else None,
                         "book": t.booking_url})
        return {"count": len(rows), "tee_times": rows[:40]}

    def t_answer(self, run: AgentRun, text: str) -> Any:
        run.answer = text
        return {"ok": True}

    # ---------------------------------------------------------------- loop
    def _loop(self, system: str, tools: list[dict], prompt: str, done) -> AgentRun:
        if self.client is None:
            import anthropic

            self.client = anthropic.Anthropic()
        run = AgentRun()
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        for turn in range(self.max_turns):
            resp = self.client.messages.create(model=self.model, max_tokens=2048, system=system, tools=tools,
                                               messages=messages)
            run.usage["turns"] = turn + 1
            if getattr(resp, "usage", None):
                run.usage["input_tokens"] += resp.usage.input_tokens or 0
                run.usage["output_tokens"] += resp.usage.output_tokens or 0
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            if not uses:
                break
            results = []
            for b in uses:
                fn = getattr(self, f"t_{b.name}", None)
                try:
                    if fn is None:
                        raise ValueError(f"unknown tool {b.name}")
                    out, ok = fn(run, **(b.input or {})), True
                except Exception as e:  # noqa: BLE001 — errors go back to the model
                    out, ok = {"error": f"{type(e).__name__}: {e}"}, False
                run.tool_calls.append((b.name, dict(b.input or {}), ok))
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, default=str), "is_error": not ok})
            messages.append({"role": "user", "content": results})
            if done(run):
                break
        return run

    def _ctx(self) -> dict[str, str]:
        return {"today": self.today.isoformat(), "weekday": DAYS[self.today.weekday()], "tz": self.cfg.timezone}

    def compile_watch(self, request: str) -> AgentRun:
        return self._loop(ADD_SYSTEM.format(**self._ctx()), ADD_TOOLS, request, lambda r: r.rule is not None)

    def ask(self, question: str) -> AgentRun:
        return self._loop(ASK_SYSTEM.format(**self._ctx()), ASK_TOOLS, question, lambda r: r.answer is not None)


def today_in(tz: str) -> date:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(tz)).date()
