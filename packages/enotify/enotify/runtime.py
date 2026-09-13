"""Provider-agnostic runtime and scheduler contracts."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
import threading
import time

from .providers.events.interface import EventOccurrence


@runtime_checkable
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


@runtime_checkable
class RuntimeBackend(Protocol):
    """Shared provider backend contract checked before entering the service loop."""

    def start(self) -> None: ...
    def next_deadline(self, observed_at: int) -> int | None: ...
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
    binding_cleanup: bool = True
    _started: bool = False
    _stopped: bool = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self.runtime, name)

    def start(self) -> None:
        if self._started:
            return
        self.runtime.start()
        self._started = True

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        if self.binding_cleanup:
            self.runtime.stop()
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
        self._backends: dict[tuple[str, ...], tuple[EventRuntime, int]] = {}
        self._factories: dict[tuple[str, str], Callable[..., EventRuntime]] = {}

    def register(self, provider: str, capability: str, factory: Callable[..., EventRuntime]) -> None:
        if not callable(factory):
            raise TypeError(f"runtime factory for {provider}/{capability} is not callable")
        self._factories[(provider, capability)] = factory

    @staticmethod
    def _require_runtime_methods(runtime: Any, kind: str, methods: tuple[str, ...]) -> None:
        missing = [name for name in methods if not callable(getattr(runtime, name, None))]
        if missing:
            raise TypeError(f"{kind} is missing required runtime methods: {', '.join(missing)}")

    def bind(self, provider: Any, **kwargs: Any) -> RuntimeHandle:
        factory = self._factories.get((provider.provider, provider.capability))
        # A provider without a registered backend is itself the generic
        # runtime. Keep that runtime subscription-scoped: unlike a
        # provider-owned backend, it carries the provider's bound config.
        subscription = kwargs.get("subscription")
        subscription_id = subscription.get("id") if isinstance(subscription, dict) else None
        scope = ("subscription", str(subscription_id)) if factory is None and subscription_id else ("shared",)
        key = (provider.provider, provider.capability, getattr(provider, "source", "default"), *scope)
        current = self._backends.get(key)
        if current is None:
            runtime = factory(provider=provider, wake=self.wake.signal, **kwargs) if factory else GenericProviderRuntime(provider, self.wake.signal)
            self._require_runtime_methods(runtime, "runtime backend", ("start", "next_deadline", "health", "stop"))
            runtime.start()
            current = (runtime, 0)
        runtime, refs = current
        has_binding = hasattr(runtime, "bind")
        try:
            binding = runtime.bind(**kwargs) if has_binding else runtime
            if not has_binding:
                self._require_runtime_methods(binding, "runtime", ("start", "observe", "advance", "next_deadline", "ack", "health", "stop"))
            else:
                self._require_runtime_methods(binding, "runtime binding", ("start", "observe", "advance", "next_deadline", "ack", "health", "stop"))
        except Exception:
            if refs == 0:
                runtime.stop()
            raise
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
        return RuntimeHandle(binding, release, has_binding, _started=not has_binding)

    def deadlines(self, wall_now: int) -> list[int]:
        return [deadline for runtime, _ in self._backends.values()
                if (deadline := runtime.next_deadline(wall_now)) is not None]

    def wait_timeout(self, interval: float, wall_now: float, monotonic_now: float) -> float:
        """Convert provider wall-clock deadlines into one monotonic wait."""
        waits = [max(0.0, float(deadline) - wall_now) for deadline in self.deadlines(int(wall_now))]
        deadline_mono = monotonic_now + min(waits) if waits else None
        return interval if deadline_mono is None else max(0.0, min(interval, deadline_mono - monotonic_now))

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
