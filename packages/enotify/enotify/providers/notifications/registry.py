"""Role-qualified notification-provider registry."""
from __future__ import annotations

from collections.abc import Iterable
from .interface import NotificationProvider


class NotificationRegistry:
    def __init__(self, providers: Iterable[NotificationProvider] = ()):
        self._providers: dict[tuple[str, str], NotificationProvider] = {}
        for provider in providers:
            if provider.role != "notification":
                raise ValueError(f"wrong provider role: {provider.role}")
            key = (provider.provider, provider.capability)
            if key in self._providers:
                raise ValueError(f"duplicate notification provider: {provider.provider}/{provider.capability}")
            self._providers[key] = provider

    def get(self, provider: str, notification_type: str) -> NotificationProvider:
        try:
            return self._providers[(provider, notification_type)]
        except KeyError:
            raise KeyError(f"unknown notification provider: {provider}/{notification_type}") from None

    def describe(self) -> list[dict[str, object]]:
        return [self._providers[key].describe() for key in sorted(self._providers)]


def default_registry() -> NotificationRegistry:
    from .buzz import BuzzMessageProvider

    return NotificationRegistry((BuzzMessageProvider(),))
