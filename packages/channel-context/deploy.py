#!/usr/bin/env python3
"""Install or remove channel-context for Codex, custom-grok-acp, and Claude Code."""

import argparse
import json
import os
import selectors
import shlex
import shutil
import stat
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
DEFAULT_CLAUDE_CONFIG_DIR = Path("/var/lib/buzz/claude-code")
DEFAULT_CODEX_HOME = Path("/var/lib/buzz/codex/agent-1/.codex")
DEFAULT_CODEX_BIN = "/home/ec2-user/.local/bin/codex"
DEFAULT_HOOK = Path("/var/lib/buzz-server/channel-context.py")
NEW_FILE_MODE = 0o644


def load(path: Path) -> dict:
    if not path.exists():
        return {"hooks": {}}
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or not isinstance(value.get("hooks", {}), dict):
        raise ValueError(f"{path.name} must contain a JSON object with an object-valued hooks field")
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


def _chown_quietly(path: Path, uid: int, gid: int) -> None:
    """Best-effort chown: only root can hand a file to another user, and that is fine."""
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass


def _inherit_identity(target: Path, temporary: Path) -> None:
    """Give the replacement file the identity the agent runtime expects to read.

    `tempfile.mkstemp` creates 0600 files owned by the calling user, so a deploy run under
    `sudo` would otherwise turn a config the agent user reads into a root-only file. Nothing
    errors when that happens: the runtime simply stops invoking the hook and context goes
    missing silently. An existing file keeps its exact mode and ownership; a new one inherits
    the containing directory's ownership so the directory's owner can still read it.
    """
    try:
        if target.exists():
            source = target.stat()
            os.chmod(temporary, stat.S_IMODE(source.st_mode))
            _chown_quietly(temporary, source.st_uid, source.st_gid)
        else:
            os.chmod(temporary, NEW_FILE_MODE)
            parent = target.parent.stat()
            _chown_quietly(temporary, parent.st_uid, parent.st_gid)
    except OSError as exc:
        sys.stderr.write(f"channel-context: could not set permissions on {target}: {exc}\n")


def write_bytes_atomic(path: Path, data: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _inherit_identity(path, temporary_path)
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
        bufsize=0,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    expected_command = shlex.quote(str(hook_path.resolve()))
    expected_source = str((home / "hooks.json").resolve())
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(requests.encode())
        process.stdin.flush()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        pending = b""
        events = selector.select(APP_SERVER_TIMEOUT_SECONDS)
        while events:
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                try:
                    response = json.loads(line.decode())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if response.get("id") != 2:
                    continue
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
                raise RuntimeError("Codex did not report the installed channel-context hook")
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
        # copy2 carries the mode across but not the owner, so a sudo run would leave a
        # root-owned backup beside a file the agent user owns.
        source = path.stat()
        _chown_quietly(backup_path, source.st_uid, source.st_gid)


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


def python_hook_command(hook_path: Path) -> str:
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
            "hooks": [{"type": "command", "command": python_hook_command(hook_path)}],
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


def install_claude(config_dir: Path, hook_path: Path) -> None:
    """Register the hook in Claude Code user settings.

    Claude Code reads `UserPromptSubmit` from `$CLAUDE_CONFIG_DIR/settings.json` and honors the same
    `hookSpecificOutput.additionalContext` contract as Codex, so the hook script is unchanged. There
    is no hook-trust step, and `additionalContextLimit` is not a Claude Code key. `settings.json`
    also carries permissions, env, and model for every Claude agent sharing this config dir, so the
    unrelated keys `load` preserves matter more here than for the other runtimes.
    """
    config_path = config_dir / "settings.json"
    config = load(config_path)
    remove_groups(config, HOOK_NAME)
    groups = config["hooks"].setdefault(HOOK_NAME, [])
    if not isinstance(groups, list):
        raise ValueError("hooks.UserPromptSubmit must be an array")
    groups.append(
        {
            "__buzz_customization": GROUP_MARKER,
            "hooks": [{"type": "command", "command": python_hook_command(hook_path)}],
        }
    )
    config_dir.mkdir(parents=True, exist_ok=True)
    backup_once(config_path)
    write_atomic(config_path, config)


def uninstall_claude(config_dir: Path) -> None:
    config_path = config_dir / "settings.json"
    if not config_path.exists():
        return
    config = load(config_path)
    remove_groups(config, HOOK_NAME)
    write_atomic(config_path, config)


def run_step(name: str, action) -> str:
    """Run one runtime's install/uninstall, reporting rather than aborting the whole run.

    Each runtime writes to a different file with different ownership, and the Codex step also
    depends on an external process, so any one of them can fail for reasons that say nothing
    about the others. Aborting the run left earlier runtimes installed, later ones untouched,
    and no summary of which was which.
    """
    try:
        action()
    except Exception as exc:  # one runtime failing must not hide the others
        sys.stderr.write(f"channel-context: {name} failed: {type(exc).__name__}: {exc}\n")
        return "failed"
    return "ok"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("install", "uninstall"))
    # Deliberately not read from $CODEX_HOME: Codex homes here are per-agent, and inheriting the
    # variable from whichever agent runs the deploy aims the install at that agent by accident.
    parser.add_argument("--codex-home", default=str(DEFAULT_CODEX_HOME))
    parser.add_argument(
        "--custom-grok-acp-home",
        default=os.environ.get("CUSTOM_GROK_ACP_HOME", str(DEFAULT_GROK_ACP_HOME)),
    )
    parser.add_argument(
        "--claude-config-dir",
        default=os.environ.get("CLAUDE_CONFIG_DIR", str(DEFAULT_CLAUDE_CONFIG_DIR)),
    )
    parser.add_argument(
        "--context-home",
        default=os.environ.get("BUZZ_CHANNEL_CONTEXT_HOME", str(DEFAULT_CONTEXT_HOME)),
    )
    parser.add_argument("--runtime", choices=("all", "codex", "grok", "claude"), default="all")
    # The installed copy, not this checkout: a checkout path can be an agent-scoped working
    # directory that later disappears, and the hook fails open when its script is missing.
    parser.add_argument("--hook", default=str(DEFAULT_HOOK))
    parser.add_argument("--codex-bin", default=os.environ.get("CODEX_PATH", DEFAULT_CODEX_BIN))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    hook_path = Path(args.hook)
    if args.action == "install":
        ensure_context_home(Path(args.context_home))
        steps = (
            ("claude", lambda: install_claude(Path(args.claude_config_dir), hook_path)),
            ("codex", lambda: install_codex(Path(args.codex_home), hook_path, args.codex_bin)),
            ("grok", lambda: install_grok(Path(args.custom_grok_acp_home), hook_path)),
        )
    else:
        steps = (
            ("claude", lambda: uninstall_claude(Path(args.claude_config_dir))),
            ("codex", lambda: uninstall_codex(Path(args.codex_home))),
            ("grok", lambda: uninstall_grok(Path(args.custom_grok_acp_home))),
        )
    # Claude Code stays first: it is the only runtime whose step cannot fail on an external
    # process, so it is the one most likely to leave the host in a working state.
    failed = False
    for name, action in steps:
        if args.runtime not in ("all", name):
            continue
        status = run_step(name, action)
        failed = failed or status == "failed"
        sys.stdout.write(f"channel-context: {name} {args.action} {status}\n")
        sys.stdout.flush()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
