from __future__ import annotations

from typing import Any, Iterable
from .interface import EventOccurrence
from .schema import nonempty_string, object_config


class BuzzChannelEventsProvider:
    role = "event"
    provider = "buzz"
    capability = "channel-events"

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        value = object_config(config, version, {"community", "channel", "author", "kind"}, {"community", "channel"})
        value["community"] = nonempty_string(value["community"], "community")
        value["channel"] = nonempty_string(value["channel"], "channel")
        if "author" in value:
            value["author"] = nonempty_string(value["author"], "author")
        if "kind" in value and (not isinstance(value["kind"], int) or isinstance(value["kind"], bool) or value["kind"] < 0):
            raise ValueError("kind must be a non-negative integer")
        return value

    def observe(self, cursor: str | None = None) -> Iterable[EventOccurrence]:
        return ()
