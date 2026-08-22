#!/usr/bin/env python3
"""Deploy the repository's managed Buzz base prompt."""

import argparse
import shutil
from pathlib import Path


SOURCE = Path(__file__).with_name("base_prompt.md")
DEFAULT_DESTINATION = Path("/var/lib/buzz-server/base_prompt.md")


def install(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, destination)


def uninstall(destination: Path) -> None:
    destination.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument(
        "--destination",
        type=Path,
        default=DEFAULT_DESTINATION,
        help="destination file (defaults to the live Buzz base prompt)",
    )
    args = parser.parse_args()
    if args.action == "install":
        install(args.destination)
    else:
        uninstall(args.destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
