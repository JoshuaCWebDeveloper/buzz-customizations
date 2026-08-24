#!/usr/bin/env python3
"""Drop-in grok-acp wrapper with a hook interface for prompt and context control."""

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
MAX_HOOK_OUTPUT_BYTES = 256 * 1024
DEFAULT_HOME = Path("/var/lib/buzz-server/custom-grok-acp.d")
HOST_GROK_HOME = Path("/var/lib/buzz/grok")
SESSION_PROMPT = "session/prompt"


def hook_home() -> Path:
    return Path(os.environ.get("CUSTOM_GROK_ACP_HOME", str(DEFAULT_HOME)))


def hook_timeout_seconds() -> float:
    try:
        return float(os.environ.get("CUSTOM_GROK_ACP_HOOK_TIMEOUT", "5"))
    except ValueError:
        return 5.0


def _load_hooks_config(home: Path) -> dict:
    path = home / "hooks.json"
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(value, dict) and isinstance(value.get("hooks", {}), dict):
            return value
    except (OSError, UnicodeError, ValueError, TypeError):
        return {"hooks": {}}
    return {"hooks": {}}


def hook_commands(config: dict, event: str) -> list:
    groups = config.get("hooks", {}).get(event, [])
    if not isinstance(groups, list):
        return []
    commands = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        hooks = group.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if not isinstance(hook, dict):
                continue
            hook_type = hook.get("type", "command")
            if hook_type != "command":
                continue
            command = hook.get("command")
            if isinstance(command, str) and command.strip():
                commands.append(command)
    return commands


def apply_hook_output(prompt: list, output: object) -> list:
    if not isinstance(output, dict):
        return prompt
    extra = output.get("additionalContext")
    if extra is not None:
        if not isinstance(extra, str):
            return prompt
        try:
            if len(extra.encode("utf-8")) > MAX_CONTEXT_BYTES:
                return prompt
        except UnicodeError:
            return prompt
    current = list(prompt)
    replacement = output.get("prompt")
    if isinstance(replacement, list):
        current = list(replacement)
    prepend = output.get("prepend")
    if isinstance(prepend, list):
        current = list(prepend) + current
    append = output.get("append")
    if isinstance(append, list):
        current = current + list(append)
    if isinstance(extra, str) and extra:
        current = current + [{"type": "text", "text": extra}]
    return current


def _run_hook(command: str, envelope: dict) -> object:
    try:
        args = shlex.split(command)
        if not args:
            return None
        payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result = subprocess.run(
            args,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=hook_timeout_seconds(),
            check=False,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if not raw or len(raw) > MAX_HOOK_OUTPUT_BYTES:
            return None
        return json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def inject_message(message: object, commands: Optional[list] = None) -> object:
    if not isinstance(message, dict) or message.get("method") != SESSION_PROMPT:
        return message
    params = message.get("params")
    if not isinstance(params, dict):
        return message
    prompt = params.get("prompt")
    if not isinstance(prompt, list):
        return message
    if commands is None:
        commands = hook_commands(_load_hooks_config(hook_home()), SESSION_PROMPT)
    if not commands:
        return message
    current = list(prompt)
    changed = False
    for command in commands:
        envelope = {
            "method": SESSION_PROMPT,
            "params": {"sessionId": params.get("sessionId"), "prompt": current},
        }
        updated = apply_hook_output(current, _run_hook(command, envelope))
        if updated != current:
            current = updated
            changed = True
    if not changed:
        return message
    updated_params = dict(params)
    updated_params["prompt"] = current
    updated = dict(message)
    updated["params"] = updated_params
    return updated


def transform_line(line: bytes) -> bytes:
    if not line or len(line) > MAX_LINE_BYTES:
        return line
    try:
        message = json.loads(line.decode("utf-8"))
        updated = inject_message(message)
        if updated is message:
            return line
        return json.dumps(updated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (OSError, UnicodeError, ValueError, TypeError):
        return line


def ensure_grok_home() -> None:
    if "GROK_HOME" in os.environ:
        return
    if HOST_GROK_HOME.is_dir():
        os.environ["GROK_HOME"] = str(HOST_GROK_HOME)


def resolve_command(argv: list) -> list:
    inner = os.environ.get("CUSTOM_GROK_ACP_INNER") or os.environ.get("GROK_BIN")
    if inner:
        return [inner, *argv]
    grok = shutil.which("grok")
    if grok is None:
        raise FileNotFoundError("grok")
    return [grok, *argv]


def _pump(source, destination) -> None:
    while True:
        chunk = source.read(65536)
        if not chunk:
            break
        destination.write(chunk)
        destination.flush()


def _pump_stdin(destination) -> None:
    leftover = b""
    while True:
        chunk = sys.stdin.buffer.read(65536)
        if not chunk:
            if leftover:
                destination.write(leftover)
                destination.flush()
            break
        leftover += chunk
        *lines, leftover = leftover.split(b"\n")
        for line in lines:
            destination.write(transform_line(line.rstrip(b"\r")) + b"\n")
            destination.flush()


def main(argv: Optional[list] = None) -> int:
    ensure_grok_home()
    try:
        command = resolve_command(sys.argv[1:] if argv is None else argv)
    except FileNotFoundError:
        sys.stderr.write("custom-grok-acp: grok not found; set CUSTOM_GROK_ACP_INNER or GROK_BIN\n")
        return 127
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        sys.stderr.write(f"custom-grok-acp: failed to spawn {command[0]}: {exc}\n")
        return 127
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None

    def forward(signum, _frame):
        if process.poll() is None:
            process.send_signal(signum)

    signal.signal(signal.SIGINT, forward)
    signal.signal(signal.SIGTERM, forward)
    stdout_thread = threading.Thread(target=_pump, args=(process.stdout, sys.stdout.buffer), daemon=True)
    stderr_thread = threading.Thread(target=_pump, args=(process.stderr, sys.stderr.buffer), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        _pump_stdin(process.stdin)
    except BrokenPipeError:
        pass
    try:
        process.stdin.close()
    except BrokenPipeError:
        pass
    code = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return 0 if code == 0 else (code if code is not None else 1)


if __name__ == "__main__":
    raise SystemExit(main())
