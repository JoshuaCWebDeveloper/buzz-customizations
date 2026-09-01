"""Strict, role-safe domain models and deterministic provider envelopes."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Mapping
import json, re

FREQUENCIES = {"one", "all"}
STATES = {"active", "paused", "finished", "dead", "deleted"}

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

def validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        raise ValueError("config must be an object")
    return dict(config)

@dataclass(frozen=True)
class EventTriggerSpec:
    provider: str
    event_type: str
    schema_version: int
    match: Mapping[str, Any]
    secret_refs: tuple[str, ...] = ()
    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.provider): raise ValueError("invalid event provider")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.event_type): raise ValueError("invalid event_type")
        if self.schema_version < 1: raise ValueError("schema_version must be positive")
        validate_config(self.match)
    def envelope(self) -> dict[str, Any]:
        out = {"provider": self.provider, "event_type": self.event_type, "schema_version": self.schema_version, "match": dict(self.match)}
        if self.secret_refs: out["secret_refs"] = list(self.secret_refs)
        return out

@dataclass(frozen=True)
class NotificationAddressSpec:
    provider: str
    notification_type: str
    schema_version: int
    address: Mapping[str, Any]
    secret_refs: tuple[str, ...] = ()
    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.provider): raise ValueError("invalid notification provider")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", self.notification_type): raise ValueError("invalid notification_type")
        if self.schema_version < 1: raise ValueError("schema_version must be positive")
        validate_config(self.address)
    def envelope(self) -> dict[str, Any]:
        out = {"provider": self.provider, "notification_type": self.notification_type, "schema_version": self.schema_version, "address": dict(self.address)}
        if self.secret_refs: out["secret_refs"] = list(self.secret_refs)
        return out

@dataclass(frozen=True)
class Subscription:
    id: str
    revision: int
    frequency: str
    event_trigger: EventTriggerSpec
    notification_address: NotificationAddressSpec
    state: str = "active"
    created_at: str = ""
    updated_at: str = ""
    reason: str | None = None
    def __post_init__(self):
        if self.frequency not in FREQUENCIES: raise ValueError("frequency must be one or all")
        if self.state not in STATES: raise ValueError("invalid subscription state")
        if self.revision < 1: raise ValueError("revision must be positive")
