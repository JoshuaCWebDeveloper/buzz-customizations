#!/usr/bin/env python3
"""Install or remove the custom-grok-acp drop-in command."""

import argparse
import os
import shutil
from pathlib import Path


SOURCE = Path(__file__).with_name("custom_grok_acp.py")
DEFAULT_DESTINATION = Path("/var/lib/buzz-server/custom-grok-acp")
DEFAULT_HOME = Path("/var/lib/buzz-server/custom-grok-acp.d")


def install(destination: Path, home: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, destination)
    os.chmod(destination, 0o755)
    home.mkdir(parents=True, exist_ok=True)


def uninstall(destination: Path) -> None:
    destination.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
        help="installed command path (defaults to the host custom-grok-acp path)",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(os.environ.get("CUSTOM_GROK_ACP_HOME", str(DEFAULT_HOME))),
        help="hook config directory (hooks.json); created on install, left in place on uninstall",
    )
    args = parser.parse_args()
    if args.action == "install":
        install(args.destination, args.home)
    else:
        uninstall(args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
