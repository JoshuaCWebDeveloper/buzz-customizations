"""Generic enotify service supervision and signal-safe scheduling."""
from __future__ import annotations

from collections.abc import Callable
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .models import EventTriggerSpec, NotificationAddressSpec
from .providers.events.registry import default_registry as event_registry
from .providers.notifications.registry import default_registry as notification_registry
from .runtime import RuntimeRegistry, WakeCoordinator
from .storage import Store
from .worker import Worker


class EnotifyService:
    def __init__(
        self,
        store: Store,
        runtimes: RuntimeRegistry,
        interval: float = 15.0,
        wall_clock: Callable[[], float] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store
        self.runtimes = runtimes
        self.interval = max(0.0, interval)
        self.wall_clock = wall_clock or time.time
        self.monotonic_clock = monotonic_clock or time.monotonic
        self.stopping = False
        self.bindings: dict[str, tuple[tuple[Any, ...], Any]] = {}
        self.reported_health: dict[tuple[str, str], Any] = {}

    def stop(self, *_args: Any) -> None:
        self.stopping = True
        self.runtimes.wake.signal()

    def run(self) -> int:
        previous = {}
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, self.stop)
        try:
            while not self.stopping:
                self.step()
                timeout = self.runtimes.wait_timeout(self.interval, self.wall_clock(), self.monotonic_clock())
                self.runtimes.wake.wait(timeout)
        finally:
            try:
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
            finally:
                try:
                    for _identity, binding in list(self.bindings.values()):
                        try:
                            binding.stop()
                        except Exception as exc:
                            print(f"enotify binding cleanup failed: {type(exc).__name__}", file=sys.stderr)
                    self.bindings.clear()
                finally:
                    try:
                        self.runtimes.close()
                    finally:
                        self.store.close()
        return 0

    def step(self) -> None:
        self.store.reclaim_expired()
        active_ids = set()
        for subscription in self.store.list("active"):
            active_ids.add(subscription["id"])
            try:
                event = EventTriggerSpec.from_mapping(subscription["event_trigger"])
                notification = NotificationAddressSpec.from_mapping(subscription["notification_address"])
                event_provider = event_registry().get(event.provider, event.event_type)
                event_provider = type(event_provider)(config=dict(event.match))
                notification_provider = notification_registry().get(notification.provider, notification.notification_type)
                notification_provider = type(notification_provider)(config=dict(notification.address))
                identity = (
                    subscription.get("revision"),
                    event.provider,
                    event.event_type,
                    getattr(event_provider, "capability", "default"),
                    getattr(event_provider, "source", event.match.get("source", "default")),
                    repr(sorted(event.match.items())),
                )
                current = self.bindings.get(subscription["id"])
                if current is not None and current[0] != identity:
                    current[1].stop()
                    del self.bindings[subscription["id"]]
                    current = None
                if current is None:
                    binding = self.runtimes.bind(event_provider, store=self.store, subscription=subscription)
                    self.bindings[subscription["id"]] = (identity, binding)
                else:
                    binding = current[1]
                Worker(self.store, binding, notification_provider, runtime_registry=self.runtimes,
                       clock=lambda: int(self.wall_clock())).process(
                    subscription,
                    notification_provider.render if hasattr(notification_provider, "render")
                    else lambda occurrence: json.dumps(occurrence.payload or {}, sort_keys=True),
                )
            except Exception as exc:
                print(f"enotify provider unavailable: {type(exc).__name__}", file=sys.stderr)
        for subscription_id in set(self.bindings) - active_ids:
            self.bindings.pop(subscription_id)[1].stop()
        for health in self.runtimes.health():
            source = (str(health.get("provider", "default")), str(health.get("source", "default")))
            current = health.get("error")
            if source in self.reported_health and self.reported_health[source] == current:
                continue
            if current:
                print(f"enotify runtime status: {current}", file=sys.stderr)
            elif source in self.reported_health:
                print("enotify runtime status: recovered", file=sys.stderr)
            self.reported_health[source] = current
