"""Provider-owned temporal projection of Buzz typing ticks."""
from __future__ import annotations

import hashlib
import json
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
        self._active = False
        self._last_tick: int | None = None
        self._last_tick_id: str | None = None
        self._expires_at: int | None = None
        self._seen: set[str] = set()
        self._cursor: str | None = None

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        value = object_config(config, version, {"community", "channel", "author", "ttl", "direction", "state"}, {"community", "channel", "author"})
        for field in ("community", "channel", "author"):
            value[field] = nonempty_string(value[field], field)
        ttl = value.get("ttl", DEFAULT_TYPING_TTL)
        if not isinstance(ttl, int) or isinstance(ttl, bool) or ttl <= 0:
            raise ValueError("ttl must be a positive integer")
        value["ttl"] = ttl
        if "direction" in value and value["direction"] not in ("started", "stopped"):
            raise ValueError("direction must be started or stopped")
        if "state" in value and value["state"] not in ("typing", "not-typing"):
            raise ValueError("state must be typing or not-typing")
        return value

    @property
    def source(self) -> str:
        return json.dumps({k: self.config[k] for k in ("community", "channel", "author", "ttl")}, sort_keys=True, separators=(",", ":"))

    def next_due(self) -> int | None:
        return self._expires_at if self._active else None

    def snapshot(self) -> dict[str, Any]:
        return {"active": self._active, "last_tick_at": self._last_tick,
                "last_tick_id": self._last_tick_id, "expires_at": self._expires_at,
                "source": self.source, "cursor": self._cursor}

    def restore(self, snapshot: dict[str, Any]) -> None:
        if snapshot.get("source") != self.source:
            raise ValueError("typing projection source mismatch")
        self._active = bool(snapshot.get("active"))
        self._last_tick = snapshot.get("last_tick_at")
        self._last_tick_id = snapshot.get("last_tick_id")
        self._expires_at = snapshot.get("expires_at")
        self._cursor = snapshot.get("cursor")
        if self._last_tick_id:
            self._seen.add(self._last_tick_id)

    def _occurrence(self, direction: str, prior: str, new: str, timestamp: int) -> EventOccurrence:
        identity = f"{self.source}:{direction}:{timestamp}"
        occurrence_id = hashlib.sha256(identity.encode()).hexdigest()
        payload = {"community": self.config["community"], "channel": self.config["channel"], "author": self.config["author"], "direction": direction, "prior_state": prior, "new_state": new, "transition_at": timestamp, "semantic_transition_time": timestamp, "source": self.source}
        return EventOccurrence(self.provider, self.source, occurrence_id, str(timestamp), str(timestamp), payload)

    def advance(self, now: int) -> Iterable[EventOccurrence]:
        if not self._active or self._expires_at is None or now < self._expires_at:
            return ()
        expiry = self._expires_at
        self._active = False
        self._expires_at = None
        return (self._occurrence("stopped", "typing", "not-typing", expiry),)

    def _apply(self, event_id: str, timestamp: int, observed_at: int) -> list[EventOccurrence]:
        self._cursor = str(timestamp)
        result = list(self.advance(observed_at))
        if event_id in self._seen or (self._last_tick is not None and timestamp <= self._last_tick):
            return result
        self._seen.add(event_id)
        if timestamp + self.config["ttl"] <= observed_at:
            return result
        was_active = self._active
        self._last_tick, self._last_tick_id = timestamp, event_id
        self._expires_at = timestamp + self.config["ttl"]
        self._active = True
        if not was_active:
            result.append(self._occurrence("started", "not-typing", "typing", timestamp))
        return result

    def observe(self, cursor: str | None = None, observed_at: int | None = None) -> Iterable[EventOccurrence]:
        channel = self.config.get("channel")
        if not channel:
            return ()
        since = max(0, int(cursor) - 1) if cursor is not None else 0
        command = ["buzz", "messages", "get", "--channel", channel, "--kinds", "20002"]
        if since:
            command += ["--since", str(since)]
        result = self._runner(command, check=True, capture_output=True, text=True)
        rows = json.loads(result.stdout)
        if not isinstance(rows, list):
            raise ValueError("buzz messages get returned a non-array")
        now = self._clock() if observed_at is None else observed_at
        occurrences: list[EventOccurrence] = []
        for row in sorted((r for r in rows if isinstance(r, dict)), key=lambda r: (r.get("created_at", -1), r.get("id", ""))):
            tick = _tick(row, channel, self.config["author"])
            if tick:
                occurrences.extend(self._apply(*tick, now))
        return occurrences
