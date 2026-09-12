#!/usr/bin/env python3
"""Construct and invoke the generic enotify service."""
from __future__ import annotations

import os
from pathlib import Path

from enotify.runtime import default_runtime_registry
from enotify.service import EnotifyService
from enotify.storage import Store


def main() -> int:
    database = Path(os.environ.get("ENOTIFY_DB", str(Path.home() / ".local/state/enotify/enotify.db")))
    interval = max(1, int(os.environ.get("ENOTIFY_POLL_SECONDS", "15")))
    store = Store(database)
    store.open()
    service = EnotifyService(store, default_runtime_registry(), interval=interval)
    return service.run()


if __name__ == "__main__":
    raise SystemExit(main())
