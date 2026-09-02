"""Interface and result types implemented only by notification providers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Protocol


@dataclass(frozen=True)
class SendResult:
    outcome: Literal["accepted", "retryable", "permanent"]
    receipt: str | None = None
    error: str | None = None

    @classmethod
    def accepted(cls, receipt: str) -> "SendResult":
        return cls("accepted", receipt=receipt)

    @classmethod
    def retryable(cls, error: str) -> "SendResult":
        return cls("retryable", error=error)

    @classmethod
    def permanent(cls, error: str) -> "SendResult":
        return cls("permanent", error=error)


class MessageContext(Protocol):
    """Role-neutral occurrence shape accepted by message renderers."""

    provider: str
    source: str
    occurrence_id: str
    payload: Mapping[str, Any] | None


class NotificationProvider(Protocol):
    provider: str
    capability: str
    role: str

    def describe(self) -> dict[str, Any]: ...
    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]: ...
    def send(self, message: str, delivery_key: str) -> SendResult: ...
