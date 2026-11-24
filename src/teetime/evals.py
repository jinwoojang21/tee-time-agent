"""Grade how well a parser turns plain English into the right watch rule."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .rules import WatchRule

EVALS = Path(__file__).resolve().parents[2] / "evals" / "requests.yaml"
WINDOW = 14


@dataclass
class CaseResult:
    id: str
    request: str
    field_results: dict[str, tuple[bool, Any, Any]] = field(default_factory=dict)  # field -> (ok, got, want)
    error: str | None = None
    tool_calls: int = 0
    tokens: tuple[int, int] = (0, 0)

    @property
    def passed(self) -> bool:
        return self.error is None and all(ok for ok, _, _ in self.field_results.values())

    @property
    def fields_ok(self) -> int:
        return sum(ok for ok, _, _ in self.field_results.values())


def grade(rule: WatchRule, expect: dict[str, Any], today: date) -> dict[str, tuple[bool, Any, Any]]:
    out: dict[str, tuple[bool, Any, Any]] = {}
    for k, want in expect.items():
        if k == "dates":
            got = sorted(d.isoformat() for d in rule.target_dates(today) if d < today + timedelta(WINDOW))
            out[k] = (got == sorted(want), got, sorted(want))
        elif k == "days":
            out[k] = (sorted(rule.days) == sorted(want) and not rule.dates, sorted(rule.days), sorted(want))
        elif k == "min_horizon":
            out[k] = (rule.horizon_days >= want, rule.horizon_days, f">={want}")
        elif k == "courses":
            out[k] = (sorted(rule.courses) == sorted(want), sorted(rule.courses), sorted(want))
        elif k == "max_price":
            out[k] = (rule.max_price is not None and abs(rule.max_price - want) < 0.01, rule.max_price, want)
        else:
            got = getattr(rule, k)
            out[k] = (got == want, got, want)
    return out


def run_eval(parse: Callable[[str, date], tuple[WatchRule | None, dict]], path: Path = EVALS) -> list[CaseResult]:
    spec = yaml.safe_load(path.read_text())
    today = date.fromisoformat(spec["today"])
    results = []
    for case in spec["cases"]:
        res = CaseResult(case["id"], case["request"])
        try:
            rule, meta = parse(case["request"], today)
            res.tool_calls = meta.get("tool_calls", 0)
            res.tokens = meta.get("tokens", (0, 0))
            if rule is None:
                res.error = "no rule proposed"
            else:
                res.field_results = grade(rule, case["expect"], today)
        except Exception as e:  # noqa: BLE001
            res.error = f"{type(e).__name__}: {e}"
        results.append(res)
    return results


def format_results(name: str, results: list[CaseResult]) -> str:
    total_fields = sum(len(r.field_results) or 1 for r in results)
    ok_fields = sum(r.fields_ok for r in results)
    lines = [f"### {name}", "", "| case | result | fields | wrong |", "|---|---|---|---|"]
    for r in results:
        wrong = r.error or ", ".join(f"{k}: got {g!r} want {w!r}" for k, (ok, g, w) in r.field_results.items()
                                     if not ok)
        lines.append(f"| {r.id} | {'PASS' if r.passed else 'FAIL'} | {r.fields_ok}/{len(r.field_results)} | "
                     f"{wrong.replace('|', '/')} |")
    lines.append(f"\n**{sum(r.passed for r in results)}/{len(results)} requests fully correct · "
                 f"{ok_fields}/{total_fields} fields correct**")
    tin, tout = sum(r.tokens[0] for r in results), sum(r.tokens[1] for r in results)
    if tin:
        lines.append(f"_tokens in/out: {tin:,}/{tout:,} · tool calls: {sum(r.tool_calls for r in results)}_")
    return "\n".join(lines)
