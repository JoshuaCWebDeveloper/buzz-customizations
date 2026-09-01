"""Notification-provider namespace; it may share provider IDs with events."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable

@dataclass(frozen=True)
class AcceptedDelivery:
    delivery_id: str

class NotificationProvider:
    role = "notification"
    def describe(self) -> dict[str, Any]: raise NotImplementedError
    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]: return config
    def send(self, message: str, delivery_key: str) -> AcceptedDelivery: raise NotImplementedError

class _Descriptor(NotificationProvider):
    provider, capability = "buzz", "message"
    def describe(self): return {"role": "notification", "provider": "buzz", "capabilities": ["message"], "schema_versions": [1]}
    def validate_config(self, config, version):
        if version != 1 or not isinstance(config, dict): raise ValueError("unsupported notification schema")
        unknown=set(config)-{"community","channel","mention"}
        if unknown: raise ValueError("unknown notification address fields: "+",".join(sorted(unknown)))
        if "community" not in config or "channel" not in config: raise ValueError("community and channel are required")
        return dict(config)
    def send(self, message, delivery_key): return AcceptedDelivery(delivery_key)

class NotificationRegistry:
    def __init__(self, providers: Iterable[NotificationProvider] = ()):
        self._providers = {(p.provider, p.capability): p for p in providers}
    def get(self, provider: str, notification_type: str) -> NotificationProvider:
        try: return self._providers[(provider, notification_type)]
        except KeyError: raise KeyError(f"unknown notification provider: {provider}/{notification_type}") from None
    def describe(self): return [p.describe() for p in sorted(self._providers.values(), key=lambda x: (x.provider, x.capability))]

def default_registry() -> NotificationRegistry:
    return NotificationRegistry((_Descriptor(),))
