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
from enotify.providers.events.typing import close_typing_streams, prune_typing_streams, wait_for_typing_activity
from enotify.providers.notifications.registry import default_registry as notification_registry
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
    try:
        while not stopping:
            store.reclaim_expired()
            active_streams = set()
            for subscription in store.list("active"):
                try:
                    event = EventTriggerSpec.from_mapping(subscription["event_trigger"])
                    notification = NotificationAddressSpec.from_mapping(subscription["notification_address"])
                    event_provider = event_registry().get(event.provider, event.event_type)
                    if event.provider == "buzz" and event.event_type == "typing-transitions":
                        active_streams.add((event.match["community"], event.match["channel"], event.match["author"]))
                    notification_provider = notification_registry().get(notification.provider, notification.notification_type)
                    event_provider = type(event_provider)(config=dict(event.match))
                    notification_provider = type(notification_provider)(config=dict(notification.address))
                    Worker(store, event_provider, notification_provider).process(
                        subscription, lambda occurrence: json.dumps(occurrence.payload or {}, sort_keys=True)
                    )
                except Exception as exc:
                    print(f"enotify provider unavailable: {type(exc).__name__}", file=sys.stderr)
            prune_typing_streams(active_streams)
            due = store.typing_due()
            timeout = interval if due is None else max(0, min(interval, due - int(time.time())))
            # A live typing reader wakes the scheduler as soon as a tick
            # arrives; durable deadlines remain the other wake boundary.
            wait_for_typing_activity(timeout)
    finally:
        close_typing_streams()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
