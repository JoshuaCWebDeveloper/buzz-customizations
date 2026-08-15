#!/usr/bin/env python3
"""Install or remove channel-context from a user Codex hooks.json."""

import argparse
import json
import os
import shlex
import shutil
from pathlib import Path

HOOK_NAME = "UserPromptSubmit"
GROUP_MARKER = "buzz-customizations/channel-context"


def load(path: Path) -> dict:
    if not path.exists():
        return {"hooks": {}}
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise ValueError("hooks.json must contain a JSON object with an object-valued hooks field")
    return value


def ours(group: object) -> bool:
    return isinstance(group, dict) and group.get("__buzz_customization") == GROUP_MARKER


def remove_groups(config: dict) -> None:
    groups = config.setdefault("hooks", {}).setdefault(HOOK_NAME, [])
    if isinstance(groups, list):
        config["hooks"][HOOK_NAME] = [group for group in groups if not ours(group)]


def install(home: Path, hook_path: Path) -> None:
    config_path = home / "hooks.json"
    config = load(config_path)
    remove_groups(config)
    groups = config["hooks"].setdefault(HOOK_NAME, [])
    if not isinstance(groups, list):
        raise ValueError("hooks.UserPromptSubmit must be an array")
    groups.append(
        {
            "__buzz_customization": GROUP_MARKER,
            "hooks": [{"type": "command", "command": shlex.quote(str(hook_path.resolve()))}],
        }
    )
    home.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        shutil.copy2(config_path, config_path.with_suffix(".json.buzz-customizations-backup"))
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def uninstall(home: Path) -> None:
    config_path = home / "hooks.json"
    config = load(config_path)
    remove_groups(config)
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--hook", default=str(Path(__file__).with_name("channel_context.py")))
    args = parser.parse_args()
    if args.action == "install":
        install(Path(args.codex_home), Path(args.hook))
    else:
        uninstall(Path(args.codex_home))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
