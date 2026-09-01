"""Role-safe persisted models and deterministic JSON canonicalization."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

FREQUENCIES = frozenset({"one", "all"})
STATES = frozenset({"active", "paused", "finished", "dead", "deleted"})
_IDENTIFIER = re.compile(r"[a-z][a-z0-9-]*\Z")


def canonical_json(value: Any) -> str:
    """Return the single persisted representation for provider-owned JSON."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    # Round-trip through the canonical encoder to reject non-JSON values and
    # detach persisted data from caller-owned mappings.
    return json.loads(canonical_json(dict(value)))


def _envelope(value: Mapping[str, Any], expected: set[str], kind: str) -> dict[str, Any]:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise ValueError(f"unknown {kind} fields: {','.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"missing {kind} fields: {','.join(sorted(missing))}")
    return dict(value)


@dataclass(frozen=True)
class EventTriggerSpec:
    provider: str
    event_type: str
    schema_version: int
    match: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.provider):
            raise ValueError("invalid event provider")
        if not _IDENTIFIER.fullmatch(self.event_type):
            raise ValueError("invalid event_type")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        object.__setattr__(self, "match", _object(self.match, "match"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EventTriggerSpec":
        data = _envelope(value, {"provider", "event_type", "schema_version", "match"}, "event spec")
        return cls(**data)

    def envelope(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "event_type": self.event_type,
            "schema_version": self.schema_version,
            "match": dict(self.match),
        }


@dataclass(frozen=True)
class NotificationAddressSpec:
    provider: str
    notification_type: str
    schema_version: int
    address: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.provider):
            raise ValueError("invalid notification provider")
        if not _IDENTIFIER.fullmatch(self.notification_type):
            raise ValueError("invalid notification_type")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        object.__setattr__(self, "address", _object(self.address, "address"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "NotificationAddressSpec":
        data = _envelope(
            value,
            {"provider", "notification_type", "schema_version", "address"},
            "notification spec",
        )
        return cls(**data)

    def envelope(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "notification_type": self.notification_type,
            "schema_version": self.schema_version,
            "address": dict(self.address),
        }


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

    def __post_init__(self) -> None:
        if self.frequency not in FREQUENCIES:
            raise ValueError("frequency must be one or all")
        if self.state not in STATES:
            raise ValueError("invalid subscription state")
        if self.revision < 1:
            raise ValueError("revision must be positive")
