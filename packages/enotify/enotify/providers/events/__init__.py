"""Event-provider namespace; intentionally separate from notifications."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable

@dataclass(frozen=True)
class EventOccurrence:
    occurrence_id: str
    source: str
    observed_at: str
    cursor: str | None = None
    payload: dict[str, Any] | None = None

class EventProvider:
    role = "event"
    def describe(self) -> dict[str, Any]: raise NotImplementedError
    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]: return config
    def observe(self, cursor: str | None = None) -> Iterable[EventOccurrence]: raise NotImplementedError

class _Descriptor(EventProvider):
    def __init__(self, provider: str, capability: str):
        self.provider, self.capability = provider, capability
    def describe(self): return {"role": "event", "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}
    def validate_config(self, config, version):
        if version != 1 or not isinstance(config, dict): raise ValueError("unsupported event schema")
        allowed = {"channel-events": {"community","channel","author","kind"}, "check": {"repository","check","pull_request"},"exited": {"pid","start_identity","stdout_path","stderr_path"}}
        unknown=set(config)-allowed[self.capability]
        if unknown: raise ValueError("unknown event match fields: "+",".join(sorted(unknown)))
        if self.capability == "channel-events" and not isinstance(config.get("community"), str): raise ValueError("community is required")
        if self.capability == "check" and not isinstance(config.get("check"), dict): raise ValueError("check object is required")
        if self.capability == "exited":
            if not isinstance(config.get("pid"), int) or config["pid"] <= 0: raise ValueError("positive pid is required")
            if not isinstance(config.get("start_identity"), str) or not config["start_identity"]: raise ValueError("start_identity is required")
        return dict(config)
    def observe(self, cursor=None): return ()

class EventRegistry:
    def __init__(self, providers: Iterable[EventProvider] = ()):
        self._providers = {}
        for p in providers:
            key=(p.provider,p.capability)
            if key in self._providers: raise ValueError(f"duplicate event provider: {p.provider}/{p.capability}")
            self._providers[key]=p
    def get(self, provider: str, event_type: str) -> EventProvider:
        try: return self._providers[(provider, event_type)]
        except KeyError: raise KeyError(f"unknown event provider: {provider}/{event_type}") from None
    def describe(self): return [p.describe() for p in sorted(self._providers.values(), key=lambda x: (x.provider, x.capability))]

def default_registry() -> EventRegistry:
    return EventRegistry((_Descriptor("buzz", "channel-events"), _Descriptor("github", "check"), _Descriptor("system-process", "exited")))
