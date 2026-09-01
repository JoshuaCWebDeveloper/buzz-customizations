"""Provider-owned temporal projection of Buzz typing ticks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable

from .interface import EventOccurrence
from .schema import nonempty_string, object_config

DEFAULT_TYPING_TTL = 8
STREAM_BACKLOG_LIMIT = 2048


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
                 clock: Callable[[], int] | None = None, stream: "BuzzTypingLiveStream | None" = None):
        self._runner = runner or subprocess.run
        self.config = self.validate_config(dict(config or {}), 1) if config is not None else {}
        self._clock = clock or (lambda: int(time.time()))
        self._stream = stream or (_RunnerTypingLiveStream(self._runner, self.config)
                                  if runner is not None else None)

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
        """Read bounded live ticks; projection mutation belongs to Store.

        Typing events are ephemeral and are deliberately never read from the
        persisted message endpoint.  The shared live stream is supervised by
        ``BuzzTypingLiveStream`` and fans raw events out to TTL-specific
        projections.
        """
        channel = self.config.get("channel")
        if not channel:
            return []
        self._verify_community(channel)
        stream = self._stream or _stream_pool.stream(
            self.config["community"], channel, self.config["author"]
        )
        rows = stream.poll()
        minimum = max(0, int(cursor) - 1) if cursor is not None else 0
        return sorted(
            (row for row in rows if _tick(row, channel, self.config["author"])
             and row["created_at"] >= minimum),
            key=lambda row: (row["created_at"], row["id"]),
        )

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


class BuzzTypingLiveStream:
    """Non-blocking, restartable JSONL event stream for one observation group.

    The child is shared by all TTL projections for the same community/channel/
    author.  ``poll`` never waits for relay input; it drains currently
    available lines and reconnects on EOF with bounded backoff.  The optional
    ``popen_factory`` is intentionally injectable for deterministic tests.
    """

    def __init__(self, community: str, channel: str, author: str,
                 popen_factory: Callable[..., Any] | None = None,
                 clock: Callable[[], float] | None = None):
        self.community, self.channel, self.author = community, channel, author
        self._popen = popen_factory or subprocess.Popen
        self._clock = clock or time.monotonic
        self._child: Any = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._backlog: deque[dict[str, Any]] = deque(maxlen=STREAM_BACKLOG_LIMIT)
        self._backoff_until = 0.0
        self._backoff = 1.0
        self._ready = False
        self._reconnect_requested = threading.Event()
        self._thread = threading.Thread(target=self._supervise, name="buzz-typing-stream", daemon=True)
        self._thread.start()

    @property
    def command(self) -> list[str]:
        filter_json = json.dumps({"kinds": [20002], "authors": [self.author], "#h": [self.channel]}, separators=(",", ":"))
        return ["buzz-server", "events", "subscribe", "--community", self.community, "--filter", filter_json]

    def _supervise(self) -> None:
        while not self._stop.is_set():
            delay = self._backoff_until - self._clock()
            if delay > 0 and self._stop.wait(delay):
                break
            child = None
            try:
                child = self._popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, bufsize=1)
                with self._lock:
                    self._child = child
                self._reconnect_requested.clear()
                stdout = getattr(child, "stdout", None)
                if stdout is None:
                    raise RuntimeError("typing stream child has no stdout")
                for line in stdout:
                    if self._stop.is_set():
                        break
                    self._consume(line)
                    if self._reconnect_requested.is_set():
                        break
                if self._stop.is_set():
                    break
            except Exception:
                # Keep transport details bounded and out of notifications; the
                # service remains healthy and the next retry is scheduled.
                pass
            finally:
                self._close_child(child)
            with self._lock:
                self._ready = False
                self._backoff_until = self._clock() + self._backoff
                self._backoff = min(self._backoff * 2, 30.0)

    def _consume(self, line: str) -> None:
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            return
        if not isinstance(value, dict):
            return
        kind = value.get("type")
        if kind == "eose":
            with self._lock:
                self._ready = True
                self._backoff = 1.0
            self._wake.set()
            return
        if kind in ("closed", "error"):
            # Closing the stdout pipe causes the supervisor to reconnect.
            self._reconnect_requested.set()
            return
        if kind != "event" or not isinstance(value.get("event"), dict):
            return
        with self._lock:
            self._backlog.append(value["event"])
        self._wake.set()

    def poll(self) -> list[dict[str, Any]]:
        # The reader owns the blocking pipe read; this short handoff only
        # covers thread scheduling and does not wait for relay input.
        self._wake.wait(0.05)
        # Return the bounded overlap buffer to every consumer.  This is the
        # fan-out boundary: different TTL providers share one child stream but
        # independently apply the same raw ticks to their durable sources.
        with self._lock:
            return list(self._backlog) if self._ready else []

    def _close_child(self, child: Any) -> None:
        with self._lock:
            if child is self._child:
                self._child = None
        if child is None:
            return
        stdout = getattr(child, "stdout", None)
        try:
            child.terminate()
            child.wait(timeout=1)
        except Exception:
            try:
                child.kill()
            except Exception:
                pass
        if stdout is not None:
            try:
                stdout.close()
            except Exception:
                pass

    def wait(self, timeout: float) -> bool:
        signaled = self._wake.wait(max(0.0, timeout))
        if signaled:
            self._wake.clear()
        return signaled

    def close(self) -> None:
        self._stop.set()
        with self._lock:
            child = self._child
        self._close_child(child)
        self._wake.set()
        self._thread.join(timeout=2)


class _RunnerTypingLiveStream:
    """Callable-runner seam for tests; production uses ``BuzzTypingLiveStream``."""

    def __init__(self, runner: Callable[..., Any], config: dict[str, Any]):
        self._runner, self._config = runner, config

    def poll(self) -> list[dict[str, Any]]:
        channel = self._config["channel"]
        filter_json = json.dumps({"kinds": [20002], "authors": [self._config["author"]], "#h": [channel]}, separators=(",", ":"))
        command = ["buzz-server", "events", "subscribe", "--community", self._config["community"], "--filter", filter_json]
        result = self._runner(command, check=True, capture_output=True, text=True)
        text = getattr(result, "stdout", "")
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            value = []
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        rows = []
        for line in str(text).splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get("type") == "event" and isinstance(item.get("event"), dict):
                rows.append(item["event"])
        return rows


class _TypingStreamPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._streams: dict[tuple[str, str, str], BuzzTypingLiveStream] = {}

    def stream(self, community: str, channel: str, author: str) -> BuzzTypingLiveStream:
        key = (community, channel, author)
        with self._lock:
            stream = self._streams.get(key)
            if stream is None:
                stream = BuzzTypingLiveStream(*key)
                self._streams[key] = stream
            return stream

    def prune(self, active: set[tuple[str, str, str]]) -> None:
        with self._lock:
            retired = [key for key in self._streams if key not in active]
            streams = [self._streams.pop(key) for key in retired]
        for stream in streams:
            stream.close()

    def wait(self, timeout: float) -> bool:
        with self._lock:
            streams = list(self._streams.values())
        if not streams:
            time.sleep(max(0.0, timeout))
            return False
        end = time.monotonic() + max(0.0, timeout)
        while time.monotonic() < end:
            for stream in streams:
                if stream.wait(min(0.25, max(0.0, end - time.monotonic()))):
                    return True
        return False

    def close_all(self) -> None:
        with self._lock:
            streams = list(self._streams.values())
            self._streams.clear()
        for stream in streams:
            stream.close()


def close_typing_streams() -> None:
    """Stop all shared typing readers during service shutdown."""
    _stream_pool.close_all()


def prune_typing_streams(active: set[tuple[str, str, str]]) -> None:
    """Retire streams whose source group no longer has an active subscriber."""
    _stream_pool.prune(active)


def wait_for_typing_activity(timeout: float) -> bool:
    """Wait for any shared reader without polling or blocking a child read."""
    return _stream_pool.wait(timeout)


_stream_pool = _TypingStreamPool()
