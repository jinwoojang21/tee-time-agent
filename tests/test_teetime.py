import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import yaml

from teetime.agent import TeeAgent
from teetime.config import Config
from teetime.evals import run_eval
from teetime.notify import format_alert, ntfy_payload, send_ntfy
from teetime.providers import ProviderNotConfigured, build_provider
from teetime.providers.json_endpoint import get_path, select_items
from teetime.rules import WatchRule
from teetime.watcher import State, run
from teetime.weather import Weather

ROOT = Path(__file__).resolve().parents[1]
FX = ROOT / "tests" / "fixtures"
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 24, 16, 45, tzinfo=ET)


def fetch(url, headers=None):
    """Offline stand-in for the TeeItUp API, serving a real captured response."""
    if "kenna.io" in url:
        assert headers and headers["x-be-alias"] == "fairfax-county-mco"
        if "facilities" in url:
            return json.loads((FX / "teeitup_facilities.json").read_text())
        if "date=2026-11-28" in url:
            return json.loads((FX / "teeitup_2026-11-28.json").read_text())
        return []
    if "open-meteo" in url:
        hours = [f"2026-11-28T{h:02d}:00" for h in range(24)]
        return {"hourly": {"time": hours, "temperature_2m": [48.0] * 24,
                           "precipitation_probability": [10] * 12 + [70] * 12, "wind_speed_10m": [5.0] * 24}}
    raise AssertionError(f"unexpected network call: {url}")


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "teewatch.yaml"
    p.write_text((ROOT / "teewatch.yaml").read_text())
    return Config.load(p)


def sat_rule(**kw):
    base = dict(name="sat", provider="fairfax", courses=["4595", "7756", "7743"], dates=["2026-11-28"],
                earliest="13:00", latest="15:00", players=4)
    return WatchRule.from_dict(base | kw)


# ------------------------------------------------------------------ provider
def test_teeitup_normalises_real_response():
    p = build_provider("fairfax", {"kind": "teeitup", "alias": "fairfax-county-mco"}, fetch=fetch)
    tts = p.search(date(2026, 11, 28), ["4595", "7756", "7743"])
    first = tts[0]
    assert first.course.name == "Laurel Hill Golf Club"
    assert first.start == datetime(2026, 11, 28, 9, 2, tzinfo=ET)          # 14:02Z -> 9:02 ET
    assert (first.spots, first.best_price(18)) == (1, 9900)
    oaks = next(t for t in tts if t.course.id == "7756" and t.spots == 4)
    assert oaks.best_price(18, "walk") == 4900 and oaks.best_price(18, "ride") == 7000
    assert oaks.best_price(9) == 3900
    assert oaks.booking_url == "https://fairfax-county-mco.book.teeitup.golf/?course=7756&date=2026-11-28"


def test_json_provider_reproduces_teeitup_from_config_only(monkeypatch):
    """The configurable provider (used for private clubs) mapped onto the TeeItUp payload."""
    monkeypatch.setenv("FAKE_ALIAS", "fairfax-county-mco")
    spec = {
        "kind": "json", "timezone": "America/New_York",
        "courses": [{"id": "4595", "name": "Laurel Hill"}],
        "request": {"url": "https://phx-api-be-east-1b.kenna.io/v2/tee-times?date={date}&facilityIds={course_id}",
                    "headers": {"x-be-alias": "${FAKE_ALIAS}"}},
        "response": {"items": "0.teetimes", "start": "teetime", "spots": "maxPlayers",
                     "price_cents": "rates.0.greenFeeCart|rates.0.greenFeeWalking", "holes": "rates.0.holes"},
    }
    tts = build_provider("x", spec, fetch=fetch).search(date(2026, 11, 28), ["4595"])
    assert len(tts) == 4 and tts[0].start.hour == 9 and tts[0].best_price() == 9900


def test_json_provider_missing_secret_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("PIEDMONT_SESSION_COOKIE", raising=False)
    spec = yaml.safe_load((ROOT / "providers" / "piedmont.example.yaml").read_text())
    with pytest.raises(ProviderNotConfigured, match="PIEDMONT_SESSION_COOKIE"):
        build_provider("piedmont", spec, fetch=fetch).search(date(2026, 11, 28), ["piedmont"])


def test_paths():
    data = [{"teetimes": [{"a": 1}, {"a": 2}]}, {"teetimes": [{"a": 3}]}]
    assert [x["a"] for x in select_items(data, "*.teetimes")] == [1, 2, 3]
    assert get_path({"x": None, "y": 5}, "x|y") == 5


# ------------------------------------------------------------------ rules
def test_rule_validation():
    with pytest.raises(ValueError, match="players"):
        sat_rule(players=6)
    with pytest.raises(ValueError, match="earliest is after latest"):
        sat_rule(earliest="15:00", latest="09:00")
    with pytest.raises(ValueError, match="unknown watch fields"):
        WatchRule.from_dict({"name": "x", "provider": "p", "courses": ["1"], "days": ["sat"], "budget": 3})


def test_target_dates():
    r = WatchRule.from_dict({"name": "w", "provider": "p", "courses": ["1"], "days": ["sat", "sun"],
                             "horizon_days": 13})
    assert [d.isoformat() for d in r.target_dates(date(2026, 11, 24))] == [
        "2026-11-28", "2026-11-29", "2026-12-05", "2026-12-06"]


# ------------------------------------------------------------------ watcher
def test_watcher_alerts_once_per_slot(cfg, tmp_path):
    cfg.watches = [sat_rule(max_price=70)]
    st = State(tmp_path / "s.json")
    r1 = run(cfg, st, now=NOW, fetch=fetch)
    assert [(m.teetime.start.strftime("%H:%M"), m.teetime.course.id) for m in r1.new] == [
        ("13:40", "4595"), ("13:55", "7756"), ("14:10", "7743")]
    assert r1.requests == 1
    r2 = run(cfg, st, now=NOW, fetch=fetch)
    assert not r2.new and r2.already_seen == 3


def test_price_walking_and_holes_filters(cfg, tmp_path):
    cfg.watches = [sat_rule(max_price=50, walking="walk")]
    got = run(cfg, State(tmp_path / "a.json"), now=NOW, fetch=fetch).new
    assert {m.teetime.course.id for m in got} == {"7756", "7743"} and all(m.price_cents == 4900 for m in got)
    cfg.watches = [sat_rule(holes=9, players=2)]
    got = run(cfg, State(tmp_path / "b.json"), now=NOW, fetch=fetch).new
    assert got and all(m.teetime.course.id in ("7756", "7743") for m in got)


def test_rain_filter_uses_forecast(cfg, tmp_path):
    cfg.watches = [sat_rule(max_rain_chance=50)]   # fake forecast: 70% from noon
    assert run(cfg, State(tmp_path / "c.json"), now=NOW, fetch=fetch).new == []


def test_past_slots_are_ignored_and_state_pruned(cfg, tmp_path):
    cfg.watches = [sat_rule()]
    st = State(tmp_path / "d.json")
    st.seen = {"fairfax:1@2026-11-01T08:00:00-04:00#old": "x"}
    late = datetime(2026, 11, 28, 14, 0, tzinfo=ET)
    r = run(cfg, st, now=late, fetch=fetch)
    assert all(m.teetime.start > late for m in r.new)
    assert "fairfax:1@2026-11-01T08:00:00-04:00#old" not in st.seen


def test_provider_failure_is_reported_not_raised(cfg, tmp_path):
    def broken(url, headers=None):
        if "tee-times" in url:
            raise TimeoutError("slow")
        return fetch(url, headers)
    cfg.watches = [sat_rule()]
    r = run(cfg, State(tmp_path / "e.json"), now=NOW, fetch=broken)
    assert r.new == [] and "TimeoutError" in r.errors[0]


# ------------------------------------------------------------------ alerts
def test_alert_payload_has_book_buttons(cfg, tmp_path):
    cfg.watches = [sat_rule()]
    new = run(cfg, State(tmp_path / "f.json"), now=NOW, fetch=fetch, weather=Weather(fetch=fetch)).new
    title, body, buttons = format_alert(new)
    assert title.startswith("⛳") and "Laurel Hill" in body and "70% rain" in body
    assert buttons[0] == ("Book 1:40 PM Laurel Hill",
                          "https://fairfax-county-mco.book.teeitup.golf/?course=4595&date=2026-11-28")
    payload = ntfy_payload(new, "my-topic")
    assert payload["actions"][0]["action"] == "view" and len(payload["actions"]) <= 3
    sent = []
    assert send_ntfy(new, topic="my-topic", sender=lambda url, body, headers: sent.append((url, body)))
    assert sent[0][0] == "https://ntfy.sh" and json.loads(sent[0][1])["topic"] == "my-topic"


def test_no_topic_no_alert(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert send_ntfy([], sender=lambda *a: pytest.fail("should not send")) is False


# ------------------------------------------------------------------ agent (scripted fake client)
def _b(**kw):
    return SimpleNamespace(**kw)


class FakeClient:
    def __init__(self, turns):
        self.turns, self.calls = iter(turns), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=next(self.turns), usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def test_agent_fixes_its_own_invalid_rule_then_proposes(cfg):
    turns = [
        [_b(type="tool_use", id="1", name="calendar", input={"days": 7}),
         _b(type="tool_use", id="2", name="list_courses", input={})],
        [_b(type="tool_use", id="3", name="propose_watch", input={
            "name": "sat", "provider": "fairfax", "courses": ["7756"], "dates": ["2026-11-28"], "players": 5})],
        [_b(type="tool_use", id="4", name="propose_watch", input={
            "name": "sat", "provider": "fairfax", "courses": ["7756"], "dates": ["2026-11-28"], "players": 4,
            "latest": "10:00"})],
    ]
    agent = TeeAgent(cfg, date(2026, 11, 24), "test-model", client=FakeClient(turns), fetch=fetch)
    r = agent.compile_watch("Saturday before 10 for 4 at Twin Lakes Oaks")
    assert r.rule and r.rule.players == 4 and r.rule.latest == "10:00"
    assert [ok for _, _, ok in r.tool_calls] == [True, True, False, True]
    cal = json.loads(agent.client.calls[1]["messages"][2]["content"][0]["content"])
    assert cal[0] == "2026-11-24 tue" and cal[4] == "2026-11-28 sat"


def test_agent_ask_searches_live_times(cfg):
    turns = [
        [_b(type="tool_use", id="1", name="search_tee_times",
            input={"provider": "fairfax", "date": "2026-11-28", "courses": ["7756"], "players": 4})],
        [_b(type="tool_use", id="2", name="answer", input={"text": "1:55 PM at Twin Lakes Oaks, $49 walking"})],
    ]
    agent = TeeAgent(cfg, date(2026, 11, 24), "m", client=FakeClient(turns), fetch=fetch)
    r = agent.ask("foursome Saturday at Twin Lakes?")
    rows = json.loads(agent.client.calls[1]["messages"][2]["content"][0]["content"])["tee_times"]
    assert rows[0]["time"] == "Sat 1:55 PM" and rows[0]["spots"] == 4 and r.answer.startswith("1:55")


# ------------------------------------------------------------------ config + eval harness
def test_config_add_and_remove_watch(cfg):
    cfg.add_watch(sat_rule(name="new-one"))
    assert "new-one" in [w["name"] for w in yaml.safe_load(cfg.path.read_text())["watches"]]
    with pytest.raises(ValueError, match="already exists"):
        cfg.add_watch(sat_rule(name="new-one"))
    assert cfg.remove_watch("new-one")


def test_baseline_eval_runs():
    from teetime.baseline import parse

    res = run_eval(lambda r, t: (parse(r, t), {}))
    assert len(res) == 12 and 4 <= sum(r.passed for r in res) <= 10
