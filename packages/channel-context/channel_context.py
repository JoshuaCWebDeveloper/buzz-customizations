#!/usr/bin/env python3
"""Buzz channel context for Codex UserPromptSubmit and custom-grok-acp session/prompt."""

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Optional

MAX_INPUT_BYTES = 1024 * 1024
MAX_CONTEXT_BYTES = 128 * 1024
DEFAULT_CONTEXT_HOME = Path("/var/lib/buzz/channel-context")
CHANNEL_RE = re.compile(
    r"^\s*Channel:\s*[^\r\n]*?\(\#?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)\s*$",
    re.IGNORECASE,
)


def context_home() -> Path:
    override = os.environ.get("BUZZ_CHANNEL_CONTEXT_HOME")
    if override:
        return Path(override)
    return DEFAULT_CONTEXT_HOME


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


def load_context(channel_uuid: str) -> str:
    return _concat(context_home() / channel_uuid)


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


def _grok_prompt(payload: dict) -> Optional[list]:
    params = payload.get("params")
    if isinstance(params, dict) and isinstance(params.get("prompt"), list):
        return params["prompt"]
    prompt = payload.get("prompt")
    if isinstance(prompt, list):
        return prompt
    return None


def handle_codex(payload: dict) -> Optional[dict]:
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return None
    channel_uuid = _channel_uuid(prompt)
    if channel_uuid is None:
        return None
    context = load_context(channel_uuid)
    if not context:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def handle_grok(payload: dict) -> Optional[dict]:
    if payload.get("method") != "session/prompt":
        return None
    prompt = _grok_prompt(payload)
    if prompt is None or _already_injected(prompt):
        return None
    channel_uuid = _channel_uuid(_prompt_text(prompt))
    if channel_uuid is None:
        return None
    context = load_context(channel_uuid)
    if not context:
        return None
    return {"additionalContext": f"[Channel Context]\n{context}"}


def main() -> int:
    try:
        raw = _read_stdin()
        if len(raw) > MAX_INPUT_BYTES:
            return 0
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return 0
        output = handle_codex(payload)
        if output is None:
            output = handle_grok(payload)
        if output is None:
            return 0
        sys.stdout.write(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    except (OSError, UnicodeError, ValueError, TypeError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
