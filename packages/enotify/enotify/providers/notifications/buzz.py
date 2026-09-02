from __future__ import annotations

import json
import os
import string
import subprocess
from typing import Any, Callable
from .interface import MessageContext, SendResult


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


class BuzzMessageProvider:

    role = "notification"
    provider = "buzz"
    capability = "message"

    def __init__(self, config: dict[str, Any] | None = None, runner: Callable[..., Any] | None = None):
        self.config = self.validate_config(dict(config or {}), 1) if config is not None else {}
        self._runner = runner or subprocess.run

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1], "content_variables": ["author", "direction"]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        if version != 1:
            raise ValueError("unsupported notification schema version")
        if not isinstance(config, dict):
            raise ValueError("notification address must be an object")
        unknown = set(config) - {"community", "channel", "mention", "content"}
        missing = {"community", "channel"} - set(config)
        if unknown:
            raise ValueError("unknown notification address fields: " + ",".join(sorted(unknown)))
        if missing:
            raise ValueError("missing notification address fields: " + ",".join(sorted(missing)))
        normalized: dict[str, Any] = {
            "community": _text(config["community"], "community"),
            "channel": _text(config["channel"], "channel"),
        }
        if "mention" in config:
            mention = config["mention"]
            if not isinstance(mention, dict) or set(mention) != {"pubkey", "handle"}:
                raise ValueError("mention must contain stable pubkey and handle")
            normalized["mention"] = {
                "pubkey": _text(mention["pubkey"], "mention.pubkey"),
                "handle": _text(mention["handle"], "mention.handle"),
            }
        if "content" in config:
            normalized["content"] = self._validate_template(config["content"])
        return normalized

    @staticmethod
    def _validate_template(value: Any) -> str:
        template = _text(value, "content")
        try:
            parsed = list(string.Formatter().parse(template))
        except ValueError as exc:
            raise ValueError(f"invalid content template: {exc}") from None
        for _literal, field, format_spec, conversion in parsed:
            if field is None:
                continue
            if field not in {"author", "direction"} or format_spec or conversion:
                raise ValueError("content template allows only {author} and {direction}")
        return template

    def render(self, occurrence: MessageContext) -> str:
        payload = occurrence.payload or {}
        direction = payload.get("direction")
        template = self.config.get("content")
        if template is None:
            if isinstance(direction, str):
                return f"Typing {direction}"
            return f"{occurrence.provider}/{occurrence.source} event {occurrence.occurrence_id}"
        mention = self.config.get("mention")
        author = mention.get("handle", "") if isinstance(mention, dict) else ""
        return template.format_map({"author": author, "direction": direction or ""})

    def send(self, message: str, delivery_key: str) -> SendResult:
        channel = self.config.get("channel")
        if not channel:
            return SendResult.permanent("notification provider is not configured")
        try:
            result = self._runner(["buzz", "channels", "get", "--channel", channel], check=True, capture_output=True, text=True)
            value = json.loads(result.stdout)
            actual = value.get("community") or value.get("community_id") if isinstance(value, dict) else None
            actual = actual or os.environ.get("BUZZ_COMMUNITY_ID")
            if actual is None:
                return SendResult.permanent("Buzz CLI omitted community; BUZZ_COMMUNITY_ID is required")
            if actual != self.config.get("community"):
                return SendResult.permanent("Buzz channel community does not match configured community")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return SendResult.retryable(str(exc))
        command = ["buzz", "messages", "send", "--channel", channel, "--content", "-"]
        mention = self.config.get("mention")
        content = message
        if isinstance(mention, dict):
            content = f"@{mention['handle']} {message}"
            command += ["--mention", mention["pubkey"]]
        try:
            result = self._runner(command, input=content, text=True, capture_output=True, check=False)
        except OSError as exc:
            return SendResult.retryable(str(exc))
        if result.returncode != 0:
            error = (result.stderr or "buzz send failed").strip()[:500]
            return SendResult.retryable(error) if result.returncode in (2, 75) else SendResult.permanent(error)
        try:
            value = json.loads(result.stdout)
            receipt = value.get("event_id") or value.get("id")
        except (ValueError, TypeError):
            receipt = None
        if not receipt:
            return SendResult.permanent("Buzz returned no signed event receipt")
        return SendResult.accepted(str(receipt))
