"""Provider-agnostic runtime and scheduler contracts."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol
import threading
import time

from .providers.events.interface import EventOccurrence


class EventRuntime(Protocol):
    """A subscription binding to a registry-owned provider backend."""

    provider: str
    source: str

    def start(self) -> None: ...
    def observe(self, cursor: str | None, observed_at: int) -> Iterable[EventOccurrence]: ...
    def advance(self, observed_at: int) -> Iterable[EventOccurrence]: ...
    def next_deadline(self, observed_at: int) -> int | None: ...
    def ack(self, occurrence: EventOccurrence) -> None: ...
    def health(self) -> dict[str, Any]: ...
    def stop(self) -> None: ...


class WakeCoordinator:
    """One service-level wake event shared by all runtime backends."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def signal(self) -> None:
        self._event.set()

    def wait(self, timeout: float) -> bool:
        signaled = self._event.wait(max(0.0, timeout))
        if signaled:
            self._event.clear()
        return signaled


@dataclass
class RuntimeHandle:
    runtime: EventRuntime
    release: Callable[[], None]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.runtime, name)

    def stop(self) -> None:
        self.release()


class GenericProviderRuntime:
    """Runtime adapter for providers without provider-owned state."""

    def __init__(self, provider: Any, wake: Callable[[], None] | None = None):
        self.provider = provider.provider
        self.source = getattr(provider, "source", "default")
        self._provider = provider
        self._wake = wake or (lambda: None)

    def start(self) -> None:
        starter = getattr(self._provider, "start", None)
        if starter is not None:
            starter(self._wake)

    def observe(self, cursor: str | None, observed_at: int) -> Iterable[EventOccurrence]:
        return self._provider.observe(cursor)

    def advance(self, observed_at: int) -> Iterable[EventOccurrence]:
        advance = getattr(self._provider, "advance", None)
        return advance(observed_at) if advance is not None else ()

    def next_deadline(self, observed_at: int) -> int | None:
        deadline = getattr(self._provider, "next_deadline", None)
        if deadline is not None:
            return deadline(observed_at)
        due = getattr(self._provider, "next_due", None)
        return due() if due is not None else None

    def ack(self, occurrence: EventOccurrence) -> None:
        return None

    def health(self) -> dict[str, Any]:
        health = getattr(self._provider, "health", None)
        return health() if health is not None else {"ready": True, "error": None}

    def stop(self) -> None:
        closer = getattr(self._provider, "stop", None)
        if closer is not None:
            closer()


class RuntimeRegistry:
    """Constructs handles while retaining one backend per observation group."""

    def __init__(self, wake: WakeCoordinator | None = None):
        self.wake = wake or WakeCoordinator()
        self._backends: dict[tuple[str, str, str], tuple[EventRuntime, int]] = {}
        self._factories: dict[tuple[str, str], Callable[..., EventRuntime]] = {}

    def register(self, provider: str, capability: str, factory: Callable[..., EventRuntime]) -> None:
        self._factories[(provider, capability)] = factory

    def bind(self, provider: Any, **kwargs: Any) -> RuntimeHandle:
        key = (provider.provider, provider.capability, getattr(provider, "source", "default"))
        current = self._backends.get(key)
        if current is None:
            factory = self._factories.get((provider.provider, provider.capability))
            runtime = factory(provider=provider, wake=self.wake.signal, **kwargs) if factory else GenericProviderRuntime(provider, self.wake.signal)
            runtime.start()
            current = (runtime, 0)
        runtime, refs = current
        self._backends[key] = (runtime, refs + 1)

        def release() -> None:
            active = self._backends.get(key)
            if active is None:
                return
            backend, count = active
            if count > 1:
                self._backends[key] = (backend, count - 1)
            else:
                del self._backends[key]
                backend.stop()

        binding = runtime.bind(**kwargs) if hasattr(runtime, "bind") else runtime
        return RuntimeHandle(binding, release)

    def deadlines(self, now: int) -> list[int]:
        return [deadline for runtime, _ in self._backends.values()
                if (deadline := runtime.next_deadline(now)) is not None]

    def health(self) -> list[dict[str, Any]]:
        return [runtime.health() for runtime, _ in self._backends.values()]

    def close(self) -> None:
        backends = list(self._backends.values())
        self._backends.clear()
        for runtime, _ in backends:
            runtime.stop()


def default_runtime_registry(wake: WakeCoordinator | None = None) -> RuntimeRegistry:
    registry = RuntimeRegistry(wake)
    from .providers.events.typing_runtime import BuzzTypingRuntimeBackend
    registry.register("buzz", "typing-transitions", BuzzTypingRuntimeBackend)
    return registry
