#!/usr/bin/env python3
"""Drop-in grok-acp wrapper that injects Buzz channel context into session/prompt."""

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

MAX_LINE_BYTES = 8 * 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
HOST_GROK_HOME = Path("/var/lib/buzz/grok")
CHANNEL_RE = re.compile(
    r"^\s*Channel:\s*[^\r\n]*?\(\#?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)\s*$",
    re.IGNORECASE,
)


def _channel_uuid(prompt: str) -> Optional[str]:
    lines = prompt.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "[Context]":
            continue
        frame = []
        for candidate in lines[index + 1 :]:
            if not candidate.strip():
                break
            frame.append(candidate)
        if not any(re.fullmatch(r"\s*Scope:\s*(?:channel|thread)\s*", item, re.IGNORECASE) for item in frame):
            continue
        for candidate in frame:
            match = CHANNEL_RE.fullmatch(candidate)
            if match:
                return match.group(1).lower()
    return None


def _concat(directory: Path) -> str:
    if not directory.is_dir():
        return ""
    parts: list[str] = []
    total = 0
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
        for entry in entries:
            if not stat.S_ISREG(entry.lstat().st_mode):
                continue
            data = entry.read_bytes()
            total += len(data)
            if total > MAX_CONTEXT_BYTES:
                return ""
            parts.append(data.decode("utf-8"))
    except (OSError, UnicodeError):
        return ""
    return "".join(parts)


def _load(channel_uuid: str) -> str:
    override = os.environ.get("BUZZ_CHANNEL_CONTEXT_HOME")
    if override:
        return _concat(Path(override) / channel_uuid)
    homes: list[Path] = []
    grok_home = os.environ.get("GROK_HOME")
    if grok_home:
        homes.append(Path(grok_home))
    homes.append(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")))
    seen = set()
    for home in homes:
        resolved = home.resolve() if home.exists() else home
        if resolved in seen:
            continue
        seen.add(resolved)
        text = _concat(home / "channel-context" / channel_uuid)
        if text:
            return text
    return ""


def _prompt_text(prompt: list) -> str:
    parts = []
    for block in prompt:
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _already_injected(prompt: list) -> bool:
    for block in prompt:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        text = block["text"]
        if text.lstrip().startswith("[Channel Context]") or "\n[Channel Context]\n" in text:
            return True
    return False


def inject_message(message: object) -> object:
    if not isinstance(message, dict) or message.get("method") != "session/prompt":
        return message
    params = message.get("params")
    if not isinstance(params, dict):
        return message
    prompt = params.get("prompt")
    if not isinstance(prompt, list) or _already_injected(prompt):
        return message
    channel_uuid = _channel_uuid(_prompt_text(prompt))
    if channel_uuid is None:
        return message
    context = _load(channel_uuid)
    if not context:
        return message
    updated_params = dict(params)
    updated_params["prompt"] = list(prompt) + [{"type": "text", "text": f"[Channel Context]\n{context}"}]
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


def resolve_command(argv: list[str]) -> list[str]:
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


def main(argv: Optional[list[str]] = None) -> int:
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
