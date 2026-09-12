"""Event-provider API and built-in registry."""
from .interface import EventOccurrence, EventProvider
from .registry import EventRegistry, default_registry

__all__ = ["EventOccurrence", "EventProvider", "EventRegistry", "BuzzTypingTransitionsProvider", "default_registry"]


def __getattr__(name: str):
    if name == "BuzzTypingTransitionsProvider":
        from ...typing import BuzzTypingTransitionsProvider
        return BuzzTypingTransitionsProvider
    raise AttributeError(name)
