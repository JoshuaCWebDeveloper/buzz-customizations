#!/usr/bin/env python3
"""Explicit reversible state-directory scaffolding; never starts a service."""
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "ENOTIFY_MANAGED"


def install(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / MARKER
    marker.write_text(
        "Managed by packages/enotify/deploy.py. No service has been started.\n",
        encoding="utf-8",
    )


def uninstall(state_dir: Path) -> None:
    # Preserve the database, logs, backups, and every unrelated file. Removal
    # of retained state is a separate operator decision.
    (state_dir / MARKER).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument(
        "--state-dir", default=str(Path.home() / ".local/state/enotify")
    )
    args = parser.parse_args(argv)
    state_dir = Path(args.state_dir)
    if args.action == "install":
        install(state_dir)
    else:
        uninstall(state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
