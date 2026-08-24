#!/usr/bin/env python3
"""Install or remove channel-context for Codex and custom-grok-acp."""

import argparse
import json
import os
import selectors
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_NAME = "UserPromptSubmit"
GROK_EVENT = "session/prompt"
GROUP_MARKER = "buzz-customizations/channel-context"
APP_SERVER_TIMEOUT_SECONDS = 15
DEFAULT_CONTEXT_HOME = Path("/var/lib/buzz/channel-context")
DEFAULT_GROK_ACP_HOME = Path("/var/lib/buzz-server/custom-grok-acp.d")


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


def remove_groups(config: dict, event: str) -> None:
    groups = config.setdefault("hooks", {}).setdefault(event, [])
    if isinstance(groups, list):
        config["hooks"][event] = [group for group in groups if not ours(group)]


def our_hook_keys(config_path: Path, config: dict) -> list:
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


def _hook_identity(home: Path, hook_path: Path, codex_bin: str) -> tuple:
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


def ensure_context_home(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"channel-context: could not create {path}: {exc}\n")


def backup_once(path: Path) -> None:
    backup_path = path.with_name(path.name + ".buzz-customizations-backup")
    if path.exists() and not backup_path.exists():
        shutil.copy2(path, backup_path)


def install_codex(home: Path, hook_path: Path, codex_bin: str) -> None:
    config_path = home / "hooks.json"
    config = load(config_path)
    stale_trust_keys = our_hook_keys(config_path, config)
    remove_groups(config, HOOK_NAME)
    groups = config["hooks"].setdefault(HOOK_NAME, [])
    if not isinstance(groups, list):
        raise ValueError("hooks.UserPromptSubmit must be an array")
    groups.append(
        {
            "__buzz_customization": GROUP_MARKER,
            "hooks": [
                {
                    "type": "command",
                    "command": shlex.quote(str(hook_path.resolve())),
                    "additionalContextLimit": 0,
                }
            ],
        }
    )
    home.mkdir(parents=True, exist_ok=True)
    backup_once(config_path)
    write_atomic(config_path, config)
    key, current_hash = _hook_identity(home, hook_path, codex_bin)
    codex_config_path = home / "config.toml"
    backup_once(codex_config_path)
    if codex_config_path.exists():
        text = codex_config_path.read_text(encoding="utf-8")
        for stale_key in stale_trust_keys:
            text = _remove_trust_section(text, stale_key)
        write_bytes_atomic(codex_config_path, text.encode())
    _set_trust(codex_config_path, key, current_hash)


def uninstall_codex(home: Path) -> None:
    config_path = home / "hooks.json"
    config = load(config_path)
    trust_keys = our_hook_keys(config_path, config)
    remove_groups(config, HOOK_NAME)
    write_atomic(config_path, config)
    codex_config_path = home / "config.toml"
    if codex_config_path.exists():
        text = codex_config_path.read_text(encoding="utf-8")
        for key in trust_keys:
            text = _remove_trust_section(text, key)
        write_bytes_atomic(codex_config_path, text.encode())


def grok_hook_command(hook_path: Path) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(hook_path.resolve()))}"


def install_grok(home: Path, hook_path: Path) -> None:
    config_path = home / "hooks.json"
    config = load(config_path) if config_path.exists() else {"hooks": {}}
    remove_groups(config, GROK_EVENT)
    groups = config["hooks"].setdefault(GROK_EVENT, [])
    if not isinstance(groups, list):
        raise ValueError("hooks.session/prompt must be an array")
    groups.append(
        {
            "__buzz_customization": GROUP_MARKER,
            "hooks": [{"type": "command", "command": grok_hook_command(hook_path)}],
        }
    )
    home.mkdir(parents=True, exist_ok=True)
    backup_once(config_path)
    write_atomic(config_path, config)


def uninstall_grok(home: Path) -> None:
    config_path = home / "hooks.json"
    if not config_path.exists():
        return
    config = load(config_path)
    remove_groups(config, GROK_EVENT)
    write_atomic(config_path, config)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument(
        "--custom-grok-acp-home",
        default=os.environ.get("CUSTOM_GROK_ACP_HOME", str(DEFAULT_GROK_ACP_HOME)),
    )
    parser.add_argument(
        "--context-home",
        default=os.environ.get("BUZZ_CHANNEL_CONTEXT_HOME", str(DEFAULT_CONTEXT_HOME)),
    )
    parser.add_argument("--runtime", choices=("all", "codex", "grok"), default="all")
    parser.add_argument("--hook", default=str(Path(__file__).with_name("channel_context.py")))
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_PATH", "codex"))
    args = parser.parse_args()
    hook_path = Path(args.hook)
    if args.action == "install":
        ensure_context_home(Path(args.context_home))
        if args.runtime in ("all", "codex"):
            install_codex(Path(args.codex_home), hook_path, args.codex_bin)
        if args.runtime in ("all", "grok"):
            install_grok(Path(args.custom_grok_acp_home), hook_path)
    else:
        if args.runtime in ("all", "codex"):
            uninstall_codex(Path(args.codex_home))
        if args.runtime in ("all", "grok"):
            uninstall_grok(Path(args.custom_grok_acp_home))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
