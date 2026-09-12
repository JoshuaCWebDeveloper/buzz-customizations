#!/usr/bin/env python3
"""Long-lived enotify worker entry point for an explicitly configured host."""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

from enotify.models import EventTriggerSpec, NotificationAddressSpec
from enotify.providers.events.registry import default_registry as event_registry
from enotify.providers.notifications.registry import default_registry as notification_registry
from enotify.runtime import default_runtime_registry
from enotify.storage import Store
from enotify.worker import Worker


stopping = False


def stop(_signum, _frame):
    global stopping
    stopping = True


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    database = Path(os.environ.get("ENOTIFY_DB", str(Path.home() / ".local/state/enotify/enotify.db")))
    interval = max(1, int(os.environ.get("ENOTIFY_POLL_SECONDS", "15")))
    store = Store(database)
    store.open()
    runtimes = default_runtime_registry()
    bindings = {}
    reported_health = {}
    try:
        while not stopping:
            store.reclaim_expired()
            active_ids = set()
            for subscription in store.list("active"):
                active_ids.add(subscription["id"])
                try:
                    event = EventTriggerSpec.from_mapping(subscription["event_trigger"])
                    notification = NotificationAddressSpec.from_mapping(subscription["notification_address"])
                    event_provider = event_registry().get(event.provider, event.event_type)
                    event_provider = type(event_provider)(config=dict(event.match))
                    notification_provider = notification_registry().get(notification.provider, notification.notification_type)
                    notification_provider = type(notification_provider)(config=dict(notification.address))
                    binding = bindings.get(subscription["id"])
                    if binding is None:
                        binding = runtimes.bind(event_provider, store=store, subscription=subscription)
                        bindings[subscription["id"]] = binding
                    Worker(store, binding, notification_provider, runtime_registry=runtimes).process(
                        subscription,
                        notification_provider.render if hasattr(notification_provider, "render")
                        else lambda occurrence: json.dumps(occurrence.payload or {}, sort_keys=True),
                    )
                except Exception as exc:
                    print(f"enotify provider unavailable: {type(exc).__name__}", file=sys.stderr)
            for subscription_id in set(bindings) - active_ids:
                bindings.pop(subscription_id).stop()
            for health in runtimes.health():
                source = str(health.get("source", "default"))
                current = health.get("error")
                if reported_health.get(source) == current:
                    continue
                if current:
                    print(f"enotify runtime status: {current}", file=sys.stderr)
                elif source in reported_health:
                    print("enotify runtime status: recovered", file=sys.stderr)
                reported_health[source] = current
            now = int(time.time())
            deadlines = runtimes.deadlines(now)
            timeout = interval if not deadlines else max(0, min(interval, min(deadlines) - now))
            runtimes.wake.wait(timeout)
    finally:
        runtimes.close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
