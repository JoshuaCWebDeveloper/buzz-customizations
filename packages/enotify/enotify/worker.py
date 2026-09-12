"""Provider-agnostic delivery orchestration; provider I/O never spans a DB transaction."""
from __future__ import annotations

from typing import Any, Callable
import time
import uuid

from .providers.events.interface import EventOccurrence
from .providers.notifications import NotificationProvider, SendResult
from .runtime import RuntimeRegistry, default_runtime_registry
from .storage import Store


class Worker:
    def __init__(
        self,
        store: Store,
        runtime: Any,
        notification_provider: NotificationProvider,
        max_attempts: int = 3,
        owner: str | None = None,
        clock: Callable[[], int] | None = None,
        runtime_registry: RuntimeRegistry | None = None,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.store = store
        self.runtime = runtime
        self.notification_provider = notification_provider
        self.max_attempts = max_attempts
        self.owner = owner or f"worker-{uuid.uuid4()}"
        self.clock = clock or (lambda: int(time.time()))
        self.runtime_registry = runtime_registry

    def process(self, subscription: dict, render: Callable[[EventOccurrence], str]) -> None:
        handle = self.runtime
        owned = False
        if not all(hasattr(handle, method) for method in ("start", "observe", "advance")):
            handle = (self.runtime_registry or default_runtime_registry()).bind(handle, store=self.store, subscription=subscription)
            owned = True
        try:
            # Service-managed handles are started when they are bound.  A
            # standalone Worker still starts an unstarted handle, while
            # repeated service passes only observe/advance it.
            if not getattr(handle, "_started", False):
                handle.start()
            observed_at = self.clock()
            cursor = self.store.checkpoint(handle.provider, handle.source)
            for occurrence in handle.advance(observed_at):
                self._process_occurrence(subscription, occurrence, render)
                handle.ack(occurrence)
            for occurrence in handle.observe(cursor, observed_at):
                self._process_occurrence(subscription, occurrence, render)
                handle.ack(occurrence)
        finally:
            if owned:
                handle.stop()

    def _process_occurrence(self, subscription: dict, occurrence: EventOccurrence,
                            render: Callable[[EventOccurrence], str]) -> None:
        current = self.store.get(subscription["id"])
        if current["state"] != "active":
            return
        occurrence_row = self.store.record_occurrence(occurrence)
        reservation = self.store.reserve(subscription["id"], occurrence_row["id"])
        if reservation is None:
            return
        self._deliver(reservation, occurrence, render)

    def retry(self, reservation_id: str, occurrence: EventOccurrence, render: Callable[[EventOccurrence], str]) -> bool:
        reservation = self.store.reservation(reservation_id)
        if reservation["state"] not in ("reserved", "retryable"):
            return False
        return self._deliver(reservation, occurrence, render)

    def _deliver(self, reservation: dict, occurrence: EventOccurrence, render: Callable[[EventOccurrence], str]) -> bool:
        while True:
            claim = self.store.claim(reservation["id"], self.owner)
            if claim is None:
                return False
            attempt = claim["attempt"]
            try:
                result = self.notification_provider.send(render(occurrence), reservation["delivery_key"])
            except Exception as exc:
                result = SendResult.retryable(str(exc))
            if result.outcome == "accepted":
                if not result.receipt:
                    result = SendResult.permanent("provider accepted without a receipt")
                else:
                    self.store.accepted(reservation["id"], attempt, result.receipt)
                    return True
            state = self.store.failed(reservation["id"], attempt, result.outcome, result.error or "provider failure", self.max_attempts)
            if state != "retryable":
                return False
            reservation = self.store.reservation(reservation["id"])
