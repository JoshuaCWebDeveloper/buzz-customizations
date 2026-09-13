"""Generic registration seam for provider-owned subscription persistence."""
from __future__ import annotations

from typing import Any, Protocol


class StorageExtension(Protocol):
    provider: str
    capability: str

    def on_create(self, transaction: Any, subscription_id: str, event: Any, stamp: str) -> None: ...
    def on_update(self, transaction: Any, old: dict[str, Any], event: Any | None, revision: int) -> None: ...
    def on_transition(self, transaction: Any, subscription: dict[str, Any], action: str, revision: int) -> None: ...


def default_extensions() -> dict[tuple[str, str], StorageExtension]:
    from .typing.storage import BuzzTypingStorageExtension
    extension = BuzzTypingStorageExtension()
    return {(extension.provider, extension.capability): extension}
