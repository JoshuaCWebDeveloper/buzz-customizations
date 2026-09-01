"""Provider-owned temporal projection of Buzz typing ticks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from typing import Any, Callable, Iterable

from .interface import EventOccurrence
from .schema import nonempty_string, object_config

DEFAULT_TYPING_TTL = 8


def _tick(row: Any, channel: str, author: str) -> tuple[str, int] | None:
    if not isinstance(row, dict) or not isinstance(row.get("id"), str):
        return None
    if row.get("kind") != 20002 or row.get("pubkey") != author:
        return None
    timestamp = row.get("created_at")
    if not isinstance(timestamp, int) or isinstance(timestamp, bool) or timestamp < 0:
        return None
    tags = row.get("tags")
    if not isinstance(tags, list) or not any(
        isinstance(tag, list) and len(tag) >= 2 and tag[0] == "h" and tag[1] == channel
        for tag in tags
    ):
        return None
    return row["id"], timestamp


class BuzzTypingTransitionsProvider:
    role = "event"
    provider = "buzz"
    capability = "typing-transitions"

    def __init__(self, runner: Callable[..., Any] | None = None, config: dict[str, Any] | None = None,
                 clock: Callable[[], int] | None = None):
        self._runner = runner or subprocess.run
        self.config = self.validate_config(dict(config or {}), 1) if config is not None else {}
        self._clock = clock or (lambda: int(time.time()))

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        value = object_config(config, version, {"community", "channel", "author", "ttl", "direction", "state", "history_limit"}, {"community", "channel", "author"})
        for field in ("community", "channel", "author"):
            value[field] = nonempty_string(value[field], field)
        ttl = value.get("ttl", DEFAULT_TYPING_TTL)
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
            raise ValueError("ttl must be a positive integer")
        value["ttl"] = ttl
        history_limit = value.get("history_limit", 1000)
        if not isinstance(history_limit, int) or isinstance(history_limit, bool) or history_limit <= 0:
            raise ValueError("history_limit must be a positive integer")
        value["history_limit"] = history_limit
        if "direction" in value and value["direction"] not in ("started", "stopped"):
            raise ValueError("direction must be started or stopped")
        if "state" in value and value["state"] not in ("typing", "not-typing"):
            raise ValueError("state must be typing or not-typing")
        return value

    @property
    def source(self) -> str:
        return json.dumps({"provider": self.provider, "event_type": self.capability, **{k: self.config[k] for k in ("community", "channel", "author", "ttl", "history_limit")}}, sort_keys=True, separators=(",", ":"))

    def next_due(self) -> int | None:
        # Durable due state is owned by Store; providers never keep a second
        # temporal projection in memory.
        return None

    def observe_ticks(self, cursor: str | None = None, observed_at: int | None = None) -> list[dict[str, Any]]:
        """Read bounded raw ticks; projection mutation belongs to Store."""
        channel = self.config.get("channel")
        if not channel:
            return []
        self._verify_community(channel)
        since = max(0, int(cursor) - 1) if cursor is not None else 0
        command = ["buzz", "messages", "get", "--channel", channel, "--limit", str(self.config["history_limit"]), "--kinds", "20002"]
        if since:
            command += ["--since", str(since)]
        result = self._runner(command, check=True, capture_output=True, text=True)
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError("buzz messages get returned a non-array")
        return sorted((row for row in rows if _tick(row, channel, self.config["author"])),
                      key=lambda row: (row["created_at"], row["id"]))

    def _verify_community(self, channel: str) -> None:
        result = self._runner(["buzz", "channels", "get", "--channel", channel], check=True, capture_output=True, text=True)
        value = json.loads(result.stdout)
        actual = value.get("community") or value.get("community_id") if isinstance(value, dict) else None
        actual = actual or os.environ.get("BUZZ_COMMUNITY_ID")
        if actual is None:
            raise ValueError("Buzz CLI omitted community; BUZZ_COMMUNITY_ID is required")
        if actual != self.config["community"]:
            raise ValueError("Buzz channel community does not match configured community")

    def transition_occurrence(self, direction: str, timestamp: int, observed_at: int) -> EventOccurrence:
        prior, new = (("typing", "not-typing") if direction == "stopped" else ("not-typing", "typing"))
        return self._occurrence(direction, prior, new, timestamp, observed_at)

    def _occurrence(self, direction: str, prior: str, new: str, timestamp: int,
                    observed_at: int | None = None) -> EventOccurrence:
        identity = f"{self.source}:{direction}:{timestamp}"
        occurrence_id = hashlib.sha256(identity.encode()).hexdigest()
        payload = {"provider": self.provider, "event_type": self.capability, "community": self.config["community"], "channel": self.config["channel"], "author": self.config["author"], "direction": direction, "prior_state": prior, "new_state": new, "transition_at": timestamp, "semantic_transition_time": timestamp, "source": self.source, "occurrence_id": occurrence_id}
        return EventOccurrence(self.provider, self.source, occurrence_id, str(observed_at if observed_at is not None else timestamp), str(timestamp), payload)

    def _matches(self, occurrence: EventOccurrence) -> bool:
        return (("direction" not in self.config or self.config["direction"] == occurrence.payload["direction"]) and
                ("state" not in self.config or self.config["state"] == occurrence.payload["new_state"]))

    def observe(self, cursor: str | None = None, observed_at: int | None = None) -> Iterable[EventOccurrence]:
        # Protocol-compatible raw observation only. Temporal synthesis is
        # intentionally implemented once, transactionally, by Store.
        return tuple(EventOccurrence(self.provider, self.source, row["id"], str(row["created_at"]), str(row["created_at"]), row)
                     for row in self.observe_ticks(cursor, observed_at))
