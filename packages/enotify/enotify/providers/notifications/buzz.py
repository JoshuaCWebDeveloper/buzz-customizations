from __future__ import annotations

import json
import subprocess
from typing import Any, Callable
from .interface import SendResult


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


class BuzzMessageProvider:

    role = "notification"
    provider = "buzz"
    capability = "message"

    def __init__(self, config: dict[str, Any] | None = None, runner: Callable[..., Any] | None = None):
        self.config = dict(config or {})
        self._runner = runner or subprocess.run

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        if version != 1:
            raise ValueError("unsupported notification schema version")
        if not isinstance(config, dict):
            raise ValueError("notification address must be an object")
        unknown = set(config) - {"community", "channel", "mention"}
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
        return normalized

    def send(self, message: str, delivery_key: str) -> SendResult:
        channel = self.config.get("channel")
        if not channel:
            return SendResult.permanent("notification provider is not configured")
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
        return SendResult.accepted(str(receipt or delivery_key))
