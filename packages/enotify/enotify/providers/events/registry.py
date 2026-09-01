"""Role-qualified event-provider registry."""
from __future__ import annotations

from collections.abc import Iterable
from .interface import EventProvider


class EventRegistry:
    def __init__(self, providers: Iterable[EventProvider] = ()):
        self._providers: dict[tuple[str, str], EventProvider] = {}
        for provider in providers:
            if provider.role != "event":
                raise ValueError(f"wrong provider role: {provider.role}")
            key = (provider.provider, provider.capability)
            if key in self._providers:
                raise ValueError(f"duplicate event provider: {provider.provider}/{provider.capability}")
            self._providers[key] = provider

    def get(self, provider: str, event_type: str) -> EventProvider:
        try:
            return self._providers[(provider, event_type)]
        except KeyError:
            raise KeyError(f"unknown event provider: {provider}/{event_type}") from None

    def describe(self) -> list[dict[str, object]]:
        return [self._providers[key].describe() for key in sorted(self._providers)]


def default_registry() -> EventRegistry:
    from .buzz import BuzzChannelEventsProvider
    from .github import GitHubCheckProvider
    from .system_process import SystemProcessExitedProvider

    return EventRegistry((BuzzChannelEventsProvider(), GitHubCheckProvider(), SystemProcessExitedProvider()))
