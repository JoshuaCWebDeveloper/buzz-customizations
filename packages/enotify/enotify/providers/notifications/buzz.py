from __future__ import annotations

from typing import Any
from .interface import SendResult


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


class BuzzMessageProvider:
    """Compile-time Buzz seam; live publication is intentionally not implemented."""

    role = "notification"
    provider = "buzz"
    capability = "message"

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
        return SendResult.permanent("live Buzz delivery adapter is not installed")
