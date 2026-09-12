"""Runtime backend for Buzz typing, including shared stream lifetime."""
from __future__ import annotations

from typing import Any, Callable
import subprocess

from ...providers.events.interface import EventOccurrence
from ...runtime import WakeCoordinator
from .typing import BuzzTypingTransitionsProvider, _stream_pool
from .typing_storage import BuzzTypingRepository


class BuzzTypingRuntimeHandle:
    def __init__(self, backend: "BuzzTypingRuntimeBackend", subscription: dict[str, Any]):
        self.backend = backend
        self.subscription = subscription
        self.provider = backend.provider.provider
        self.source = backend.provider.source

    def start(self) -> None:
        self.backend.repository.ensure_consumer(self.subscription["id"], self.source)

    def observe(self, cursor: str | None, observed_at: int) -> list[EventOccurrence]:
        ticks = self.backend.provider.observe_ticks(cursor, observed_at)
        return self.backend.repository.poll(self.backend.provider, self.subscription["id"], self.source, ticks, observed_at)

    def advance(self, observed_at: int) -> tuple[EventOccurrence, ...]:
        return ()

    def next_deadline(self, observed_at: int) -> int | None:
        return self.backend.repository.deadline()

    def ack(self, occurrence: EventOccurrence) -> None:
        self.backend.repository.advance_consumer(self.subscription["id"], self.source, occurrence.cursor, occurrence.occurrence_id)

    def health(self) -> dict[str, Any]:
        stream = getattr(self.backend.provider, "_stream", None)
        health = stream.health() if stream is not None and hasattr(stream, "health") else {"ready": True, "error": None}
        return {"provider": self.provider, "source": self.source, **health}

    def stop(self) -> None:
        # The registry owns the backend and releases it separately.
        return None


class BuzzTypingRuntimeBackend:
    def __init__(self, provider: Any, store: Any, wake: Callable[[], None] | None = None, **_: Any):
        self._key = None
        if getattr(provider, "_stream", None) is not None or getattr(provider, "_runner", subprocess.run) is not subprocess.run:
            self.provider = provider
        else:
            self._key = (provider.config["community"], provider.config["channel"], provider.config["author"])
            stream = _stream_pool.acquire(*self._key, provider.config.get("executable"))
            self.provider = BuzzTypingTransitionsProvider(config=dict(provider.config), stream=stream)
        self.store = store
        self.repository = BuzzTypingRepository(store)
        self._wake = wake or (lambda: None)

    def start(self) -> None:
        return None

    def bind(self, subscription: dict[str, Any], **_: Any) -> BuzzTypingRuntimeHandle:
        return BuzzTypingRuntimeHandle(self, subscription)

    def stop(self) -> None:
        if self._key is not None:
            _stream_pool.release(self._key)
