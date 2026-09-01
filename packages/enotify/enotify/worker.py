"""Injectable delivery orchestration; external I/O never spans a DB transaction."""
from __future__ import annotations

from typing import Callable
import uuid
import time

from .providers.events import EventOccurrence, EventProvider
from .providers.notifications import NotificationProvider, SendResult
from .storage import Store


class Worker:
    def __init__(
        self,
        store: Store,
        event_provider: EventProvider,
        notification_provider: NotificationProvider,
        max_attempts: int = 3,
        owner: str | None = None,
        clock: Callable[[], int] | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.store = store
        self.event_provider = event_provider
        self.notification_provider = notification_provider
        self.max_attempts = max_attempts
        self.owner = owner or f"worker-{uuid.uuid4()}"
        self.clock = clock or (lambda: int(time.time()))

    def next_due(self) -> int | None:
        """Return the provider deadline for a shared non-blocking scheduler."""
        due = getattr(self.event_provider, "next_due", None)
        return due() if due is not None else None

    def process(self, subscription: dict, render: Callable[[EventOccurrence], str]) -> None:
        match = subscription["event_trigger"].get("match", {})
        source = getattr(self.event_provider, "source", None) or match.get("channel") or match.get("repository") or str(match.get("pid", "default"))
        restore = getattr(self.event_provider, "restore", None)
        projection = getattr(self.store, "typing_projection", None)
        snapshot = projection(self.event_provider.provider, source) if projection else None
        if restore is not None and snapshot is not None:
            restore(snapshot)
        cursor = self.store.checkpoint(self.event_provider.provider, source)
        observe_ticks = getattr(self.event_provider, "observe_ticks", None)
        process_tick = getattr(self.store, "process_typing_tick", None)
        expire = getattr(self.store, "expire_typing", None)
        if observe_ticks is not None and process_tick is not None and expire is not None:
            self.store.ensure_typing_consumer(subscription["id"], source)
            observed_at = self.clock()
            make = self.event_provider.transition_occurrence
            matches = self.event_provider._matches
            for occurrence in expire(self.event_provider.provider, source, observed_at, make, matches):
                self._process_occurrence(subscription, occurrence, render, False)
            ttl = self.event_provider.config["ttl"]
            for tick in observe_ticks(cursor, observed_at):
                for occurrence in process_tick(self.event_provider.provider, source, tick["id"], tick["created_at"], observed_at, ttl, make, matches):
                    self._process_occurrence(subscription, occurrence, render, False)
            # Ingestion is source-scoped; every active subscription independently
            # reserves matching durable occurrences (including ones ingested by a
            # sibling subscription with another direction/address).
            for occurrence in self.store.typing_consumer_occurrences(subscription["id"], source):
                if matches(occurrence):
                    self._process_occurrence(subscription, occurrence, render, False)
                self.store.advance_typing_consumer(subscription["id"], source, occurrence.cursor)
            return
        # Due transitions are advanced before relay input, and never by a
        # provider blocking in observe(). Providers without the optional
        # boundary retain the original behavior.
        advance = getattr(self.event_provider, "advance", None)
        if advance is not None:
            for occurrence in advance(int(__import__("time").time())):
                self._process_occurrence(subscription, occurrence, render)
        for occurrence in self.event_provider.observe(cursor):
            self._process_occurrence(subscription, occurrence, render)

    def _process_occurrence(self, subscription: dict, occurrence: EventOccurrence,
                            render: Callable[[EventOccurrence], str], persist_projection: bool = True) -> None:
        current = self.store.get(subscription["id"])
        if current["state"] != "active":
            return
        snapshot = getattr(self.event_provider, "snapshot", None)
        occurrence_row = self.store.record_occurrence(occurrence, snapshot() if snapshot and persist_projection else None)
        reservation = self.store.reserve(subscription["id"], occurrence_row["id"])
        if reservation is None:
            # A one-subscription already has a selected occurrence. Never
            # silently advance to a later event.
            return
        self._deliver(reservation, occurrence, render)

    def retry(self, reservation_id: str, occurrence: EventOccurrence, render: Callable[[EventOccurrence], str]) -> bool:
        reservation = self.store.reservation(reservation_id)
        if reservation["state"] not in ("reserved", "retryable"):
            return False
        return self._deliver(reservation, occurrence, render)

    def _deliver(
        self,
        reservation: dict,
        occurrence: EventOccurrence,
        render: Callable[[EventOccurrence], str],
    ) -> bool:
        while True:
            claim = self.store.claim(reservation["id"], self.owner)
            if claim is None:
                return False
            attempt = claim["attempt"]
            try:
                result = self.notification_provider.send(
                    render(occurrence), reservation["delivery_key"]
                )
            except Exception as exc:  # provider boundary: unexpected failures are retryable
                result = SendResult.retryable(str(exc))
            if result.outcome == "accepted":
                if not result.receipt:
                    result = SendResult.permanent("provider accepted without a receipt")
                else:
                    self.store.accepted(reservation["id"], attempt, result.receipt)
                    return True
            state = self.store.failed(
                reservation["id"],
                attempt,
                result.outcome,
                result.error or "provider failure",
                self.max_attempts,
            )
            if state != "retryable":
                return False
            reservation = self.store.reservation(reservation["id"])
