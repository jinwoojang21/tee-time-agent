"""teewatch — tee-time watcher with a Claude front end."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .config import Config

STATE = Path(os.getenv("TEEWATCH_STATE", ".teewatch/state.json"))


def _cfg(a) -> Config:
    return Config.load(a.config)


def cmd_courses(a) -> int:
    cfg = _cfg(a)
    for key, p in cfg.providers().items():
        try:
            for c in p.courses():
                print(f"{key:<10} {c.id:<8} {c.name}")
        except Exception as e:  # noqa: BLE001
            print(f"{key:<10} unavailable: {e}")
    return 0


def cmd_watches(a) -> int:
    cfg = _cfg(a)
    for w in cfg.watches:
        print(yaml.safe_dump([w.to_dict()], sort_keys=False).rstrip())
    return 0


def cmd_check(a) -> int:
    from .notify import send_ntfy
    from .watcher import State, run

    cfg = _cfg(a)
    state = State(STATE)
    res = run(cfg, state, only=a.watch or None)
    for e in res.errors:
        print(f"! {e}", file=sys.stderr)
    print(f"{res.requests} tee-sheet requests · {len(res.new)} new match(es) · {res.already_seen} already alerted")
    for m in res.new:
        print(f"  [{m.watch}] {m.line()}\n      {m.teetime.booking_url}")
    if res.new and not a.dry_run:
        if send_ntfy(res.new):
            print("→ alert sent via ntfy")
        else:
            print("→ NTFY_TOPIC not set; no alert sent")
    if not a.dry_run:
        state.save()
    return 1 if res.errors and not res.requests else 0


def cmd_test_alert(a) -> int:
    from .models import Course, Offer, TeeTime
    from .notify import Match, send_ntfy

    c = Course("demo", "7756", "Twin Lakes Golf Course - Oaks Course")
    t = TeeTime(c, datetime.now(ZoneInfo("America/New_York")).replace(hour=8, minute=10, second=0, microsecond=0),
                4, offers=[Offer(18, 4900, False)],
                booking_url="https://fairfax-county-mco.book.teeitup.golf/?course=7756")
    ok = send_ntfy([Match("test-alert", t, 4900)])
    print("sent" if ok else "set NTFY_TOPIC first")
    return 0 if ok else 1


def _agent(cfg: Config):
    from .agent import TeeAgent, today_in

    return TeeAgent(cfg, today_in(cfg.timezone), os.getenv("TEEWATCH_MODEL", "claude-sonnet-5-5"))


def cmd_add(a) -> int:
    cfg = _cfg(a)
    run = _agent(cfg).compile_watch(a.request)
    if run.rule is None:
        print("Claude couldn't turn that into a watch. Try adding a day, time window or course.")
        return 1
    if a.name:
        run.rule.name = a.name
    print(yaml.safe_dump([run.rule.to_dict()], sort_keys=False))
    nxt = ", ".join(f"{d:%a %b %-d}" for d in run.rule.target_dates(_agent(cfg).today)[:6])
    print(f"Next dates watched: {nxt or '(none in range)'}")
    if not a.yes:
        if input("Save this watch? [y/N] ").strip().lower() not in ("y", "yes"):
            print("not saved")
            return 0
    cfg.add_watch(run.rule)
    print(f"saved to {cfg.path}")
    return 0


def cmd_remove(a) -> int:
    print("removed" if _cfg(a).remove_watch(a.name) else "no such watch")
    return 0


def cmd_ask(a) -> int:
    run = _agent(_cfg(a)).ask(a.question)
    print(run.answer or "(no answer)")
    return 0


def cmd_evals(a) -> int:
    from .evals import format_results, run_eval

    if a.agent == "baseline":
        from .baseline import parse

        results = run_eval(lambda r, t: (parse(r, t), {}))
    else:
        from .agent import TeeAgent

        cfg = _cfg(a)

        def claude_parse(request, today):
            run = TeeAgent(cfg, today, os.getenv("TEEWATCH_MODEL", "claude-sonnet-5-5")).compile_watch(request)
            return run.rule, {"tool_calls": len(run.tool_calls),
                              "tokens": (run.usage["input_tokens"], run.usage["output_tokens"])}

        results = run_eval(claude_parse)
    print(format_results(f"{a.agent}", results))
    return 1 if a.strict and not all(r.passed for r in results) else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("TEEWATCH_LOG", "WARNING"))
    p = argparse.ArgumentParser(prog="teewatch", description=__doc__)
    p.add_argument("--config", default=os.getenv("TEEWATCH_CONFIG", "teewatch.yaml"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("courses", help="list courses per provider").set_defaults(fn=cmd_courses)
    sub.add_parser("watches", help="show configured watches").set_defaults(fn=cmd_watches)
    c = sub.add_parser("check", help="check tee sheets now and alert on new matches (no LLM)")
    c.add_argument("--dry-run", action="store_true", help="don't alert or remember matches")
    c.add_argument("--watch", action="append", help="only this watch (repeatable)")
    c.set_defaults(fn=cmd_check)
    sub.add_parser("test-alert", help="send a sample alert to your phone").set_defaults(fn=cmd_test_alert)
    ad = sub.add_parser("add", help='describe a watch in plain English (Claude), e.g. "Saturdays before 9 for 4"')
    ad.add_argument("request")
    ad.add_argument("--name")
    ad.add_argument("-y", "--yes", action="store_true", help="save without asking")
    ad.set_defaults(fn=cmd_add)
    rm = sub.add_parser("remove", help="delete a watch")
    rm.add_argument("name")
    rm.set_defaults(fn=cmd_remove)
    q = sub.add_parser("ask", help='ask about live tee times (Claude), e.g. "best 4-ball Saturday under $60?"')
    q.add_argument("question")
    q.set_defaults(fn=cmd_ask)
    e = sub.add_parser("evals", help="grade plain-English → watch parsing")
    e.add_argument("--agent", choices=["claude", "baseline"],
                   default="claude" if os.getenv("ANTHROPIC_API_KEY") else "baseline")
    e.add_argument("--strict", action="store_true")
    e.set_defaults(fn=cmd_evals)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
