#!/usr/bin/env python3
"""Install or remove channel-context and its Codex hook trust state."""

import argparse
import json
import os
import selectors
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

HOOK_NAME = "UserPromptSubmit"
GROUP_MARKER = "buzz-customizations/channel-context"
APP_SERVER_TIMEOUT_SECONDS = 15


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


def our_hook_keys(config_path: Path, config: dict) -> list[str]:
    groups = config.get("hooks", {}).get(HOOK_NAME, [])
    if not isinstance(groups, list):
        return []
    prefix = f"{config_path.resolve()}:user_prompt_submit"
    return [
        f"{prefix}:{group_index}:{handler_index}"
        for group_index, group in enumerate(groups)
        if ours(group) and isinstance(group.get("hooks"), list)
        for handler_index, _hook in enumerate(group["hooks"])
    ]


def write_bytes_atomic(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_atomic(path: Path, config: dict) -> None:
    data = (json.dumps(config, indent=2, ensure_ascii=False) + "\n").encode()
    write_bytes_atomic(path, data)


def _hook_identity(home: Path, hook_path: Path, codex_bin: str) -> tuple[str, str]:
    requests = (
        json.dumps(
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    "clientInfo": {"name": "channel-context-deploy", "title": "channel-context deploy", "version": "1"},
                    "capabilities": None,
                },
            },
            separators=(",", ":"),
        )
        + "\n"
        + json.dumps(
            {"method": "hooks/list", "id": 2, "params": {"cwds": [str(Path.cwd())]}},
            separators=(",", ":"),
        )
        + "\n"
    )
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(home)
    process = subprocess.Popen(
        [codex_bin, "app-server"],
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    expected_command = shlex.quote(str(hook_path.resolve()))
    expected_source = str((home / "hooks.json").resolve())
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(requests)
        process.stdin.flush()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        events = selector.select(APP_SERVER_TIMEOUT_SECONDS)
        while events:
            line = process.stdout.readline()
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                events = selector.select(APP_SERVER_TIMEOUT_SECONDS)
                continue
            if response.get("id") == 2:
                for entry in response.get("result", {}).get("data", []):
                    for hook in entry.get("hooks", []):
                        if (
                            hook.get("eventName") == "userPromptSubmit"
                            and hook.get("command") == expected_command
                            and hook.get("sourcePath") == expected_source
                        ):
                            key = hook.get("key")
                            current_hash = hook.get("currentHash")
                            if isinstance(key, str) and isinstance(current_hash, str):
                                return key, current_hash
                break
            events = selector.select(APP_SERVER_TIMEOUT_SECONDS)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    raise RuntimeError("Codex did not report the installed channel-context hook")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _remove_trust_section(text: str, key: str) -> str:
    header = f'[hooks.state.{_toml_string(key)}]'
    lines = text.splitlines(keepends=True)
    start = next((index for index, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        return text
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("["):
        end += 1
    del lines[start:end]
    return "".join(lines)


def _set_trust(config_path: Path, key: str, current_hash: str) -> None:
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    text = _remove_trust_section(text, key).rstrip()
    section = f'[hooks.state.{_toml_string(key)}]\ntrusted_hash = {_toml_string(current_hash)}\n'
    updated = f"{text}\n\n{section}" if text else section
    write_bytes_atomic(config_path, updated.encode())


def install(home: Path, hook_path: Path, codex_bin: str) -> None:
    config_path = home / "hooks.json"
    config = load(config_path)
    stale_trust_keys = our_hook_keys(config_path, config)
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
    backup_path = config_path.with_suffix(".json.buzz-customizations-backup")
    if config_path.exists() and not backup_path.exists():
        shutil.copy2(config_path, backup_path)
    write_atomic(config_path, config)
    key, current_hash = _hook_identity(home, hook_path, codex_bin)
    codex_config_path = home / "config.toml"
    codex_config_backup = home / "config.toml.buzz-customizations-backup"
    if codex_config_path.exists() and not codex_config_backup.exists():
        shutil.copy2(codex_config_path, codex_config_backup)
    if codex_config_path.exists():
        text = codex_config_path.read_text(encoding="utf-8")
        for stale_key in stale_trust_keys:
            text = _remove_trust_section(text, stale_key)
        write_bytes_atomic(codex_config_path, text.encode())
    _set_trust(codex_config_path, key, current_hash)


def uninstall(home: Path) -> None:
    config_path = home / "hooks.json"
    config = load(config_path)
    trust_keys = our_hook_keys(config_path, config)
    remove_groups(config)
    write_atomic(config_path, config)
    codex_config_path = home / "config.toml"
    if codex_config_path.exists():
        text = codex_config_path.read_text(encoding="utf-8")
        for key in trust_keys:
            text = _remove_trust_section(text, key)
        write_bytes_atomic(codex_config_path, text.encode())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--hook", default=str(Path(__file__).with_name("channel_context.py")))
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_PATH", "codex"))
    args = parser.parse_args()
    if args.action == "install":
        install(Path(args.codex_home), Path(args.hook), args.codex_bin)
    else:
        uninstall(Path(args.codex_home))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
