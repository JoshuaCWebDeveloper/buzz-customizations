#!/usr/bin/env python3
"""Codex UserPromptSubmit hook for deterministic Buzz channel context."""

import json
import os
import re
import stat
from typing import Optional
import sys
from pathlib import Path

MAX_INPUT_BYTES = 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
CHANNEL_RE = re.compile(
    r"^\s*Channel:\s*[^\r\n]*?\(\#?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)\s*$",
    re.IGNORECASE,
)


def _read_stdin() -> bytes:
    return sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)


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


def _context(codex_home: Path, channel_uuid: str) -> str:
    directory = codex_home / "channel-context" / channel_uuid
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


def main() -> int:
    try:
        raw = _read_stdin()
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("hook_event_name") != "UserPromptSubmit":
            return 0
        prompt = payload.get("prompt")
        if not isinstance(prompt, str):
            return 0
        channel_uuid = _channel_uuid(prompt)
        if channel_uuid is None:
            return 0
        context = _context(Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")), channel_uuid)
        if not context:
            return 0
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        sys.stdout.write(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    except (OSError, UnicodeError, ValueError, TypeError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
