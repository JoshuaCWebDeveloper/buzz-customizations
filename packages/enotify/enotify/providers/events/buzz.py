from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable, Iterable
from .interface import EventOccurrence
from .schema import nonempty_string, object_config


class BuzzChannelEventsProvider:
    role = "event"
    provider = "buzz"
    capability = "channel-events"

    def __init__(self, runner: Callable[..., Any] | None = None, config: dict[str, Any] | None = None):
        self._runner = runner or subprocess.run
        self.config = dict(config or {})

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
        channel = self.config.get("channel")
        if not channel:
            return ()
        self._verify_community(channel)
        since = 0
        if cursor is not None:
            try:
                since = max(0, int(cursor) - 1)  # overlap protects cursor boundaries
            except ValueError:
                since = 0
        command = ["buzz", "messages", "get", "--channel", channel]
        if self.config.get("kind") is not None:
            command += ["--kinds", str(self.config["kind"])]
        if since:
            command += ["--since", str(since)]
        result = self._runner(command, check=True, capture_output=True, text=True)
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError("buzz messages get returned a non-array")
        author = self.config.get("author")
        kind = self.config.get("kind")
        occurrences = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            if author is not None and row.get("pubkey") != author:
                continue
            if kind is not None and row.get("kind") != kind:
                continue
            created = row.get("created_at")
            if not isinstance(created, int):
                continue
            occurrences.append(EventOccurrence(
                self.provider, channel, row["id"], str(created), str(created), row
            ))
        return occurrences

    def _verify_community(self, channel: str) -> None:
        result = self._runner(["buzz", "channels", "get", "--channel", channel], check=True, capture_output=True, text=True)
        value = json.loads(result.stdout)
        actual = value.get("community") or value.get("community_id") if isinstance(value, dict) else None
        actual = actual or os.environ.get("BUZZ_COMMUNITY_ID")
        if actual is None:
            raise ValueError("Buzz CLI omitted community; BUZZ_COMMUNITY_ID is required")
        if actual != self.config.get("community"):
            raise ValueError("Buzz channel community does not match configured community")
