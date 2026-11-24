from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .providers import Provider, build_provider
from .rules import WatchRule


@dataclass
class Config:
    path: Path
    timezone: str
    provider_specs: dict[str, dict[str, Any]]
    watches: list[WatchRule]
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Config:
        path = Path(path)
        raw = yaml.safe_load(path.read_text()) or {}
        specs = {}
        for key, spec in (raw.get("providers") or {}).items():
            if "file" in spec:  # e.g. providers/piedmont.yaml
                spec = {**yaml.safe_load((path.parent / spec["file"]).read_text()), **{
                    k: v for k, v in spec.items() if k != "file"}}
            specs[key] = spec
        watches = [WatchRule.from_dict(w) for w in raw.get("watches") or []]
        for w in watches:
            if w.provider not in specs:
                raise ValueError(f"watch {w.name!r}: unknown provider {w.provider!r}")
        return cls(path, raw.get("timezone", "America/New_York"), specs, watches, raw)

    def providers(self, fetch=None) -> dict[str, Provider]:
        return {k: build_provider(k, s, fetch=fetch) for k, s in self.provider_specs.items()}

    def add_watch(self, rule: WatchRule) -> None:
        if any(w.name == rule.name for w in self.watches):
            raise ValueError(f"a watch named {rule.name!r} already exists")
        self.watches.append(rule)
        self.raw.setdefault("watches", []).append(rule.to_dict())
        self.path.write_text(yaml.safe_dump(self.raw, sort_keys=False))

    def remove_watch(self, name: str) -> bool:
        before = len(self.raw.get("watches") or [])
        self.raw["watches"] = [w for w in self.raw.get("watches") or [] if w.get("name") != name]
        self.watches = [w for w in self.watches if w.name != name]
        self.path.write_text(yaml.safe_dump(self.raw, sort_keys=False))
        return len(self.raw["watches"]) < before
