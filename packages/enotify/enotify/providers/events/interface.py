"""Interface implemented only by event providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol


@dataclass(frozen=True)
class EventOccurrence:
    provider: str
    source: str
    occurrence_id: str
    observed_at: str
    cursor: str | None = None
    payload: dict[str, Any] | None = None


class EventProvider(Protocol):
    provider: str
    capability: str
    role: str

    def describe(self) -> dict[str, Any]: ...
    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]: ...
    def observe(self, cursor: str | None = None) -> Iterable[EventOccurrence]: ...
