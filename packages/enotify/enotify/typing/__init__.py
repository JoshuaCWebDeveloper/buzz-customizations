"""Buzz typing provider, runtime, and durable state boundary."""

from .provider import (
    BuzzTypingLiveStream,
    BuzzTypingTransitionsProvider,
    close_typing_streams,
    prune_typing_streams,
    typing_stream_health,
    wait_for_typing_activity,
    wake_typing_streams,
)
from .storage import BuzzTypingRepository, BuzzTypingStorageExtension, typing_source
__all__ = [
    "BuzzTypingLiveStream",
    "BuzzTypingTransitionsProvider",
    "BuzzTypingRepository",
    "BuzzTypingStorageExtension",
    "BuzzTypingRuntimeBackend",
    "BuzzTypingRuntimeHandle",
    "close_typing_streams",
    "prune_typing_streams",
    "typing_source",
    "typing_stream_health",
    "wait_for_typing_activity",
    "wake_typing_streams",
]


def __getattr__(name: str):
    if name in {"BuzzTypingRuntimeBackend", "BuzzTypingRuntimeHandle"}:
        from .runtime import BuzzTypingRuntimeBackend, BuzzTypingRuntimeHandle
        return locals()[name]
    raise AttributeError(name)
