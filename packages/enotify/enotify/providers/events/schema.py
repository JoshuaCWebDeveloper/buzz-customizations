"""Strict provider-schema helpers shared only by event implementations."""
from __future__ import annotations

from typing import Any


def object_config(config: Any, version: int, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if version != 1:
        raise ValueError("unsupported event schema version")
    if not isinstance(config, dict):
        raise ValueError("event match must be an object")
    unknown = set(config) - allowed
    missing = required - set(config)
    if unknown:
        raise ValueError("unknown event match fields: " + ",".join(sorted(unknown)))
    if missing:
        raise ValueError("missing event match fields: " + ",".join(sorted(missing)))
    return dict(config)


def nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field}.in must be a non-empty string array")
    return sorted(set(value))


def predicate(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) != 1:
        raise ValueError(f"{field} must contain exactly one of equals or in")
    if "equals" in value:
        return {"equals": nonempty_string(value["equals"], f"{field}.equals")}
    if "in" in value:
        return {"in": string_list(value["in"], field)}
    raise ValueError(f"{field} must contain exactly one of equals or in")
