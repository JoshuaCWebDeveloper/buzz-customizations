#!/usr/bin/env python3
"""Long-lived enotify worker entry point for an explicitly configured host."""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

from enotify.models import EventTriggerSpec, NotificationAddressSpec
from enotify.providers.events.registry import default_registry as event_registry
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
            for subscription in store.list("active"):
                event = EventTriggerSpec.from_mapping(subscription["event_trigger"])
                notification = NotificationAddressSpec.from_mapping(subscription["notification_address"])
                event_provider = event_registry().get(event.provider, event.event_type)
                notification_provider = notification_registry().get(notification.provider, notification.notification_type)
                # Providers receive only their own validated JSON; no credentials
                # are read from persisted specs or emitted in worker output.
                event_provider = type(event_provider)(config=dict(event.match))
                notification_provider = type(notification_provider)(config=dict(notification.address))
                Worker(store, event_provider, notification_provider).process(
                    subscription, lambda occurrence: json.dumps(occurrence.payload or {}, sort_keys=True)
                )
            time.sleep(interval)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
