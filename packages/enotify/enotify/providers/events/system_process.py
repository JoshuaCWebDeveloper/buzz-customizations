from __future__ import annotations

from typing import Any, Iterable
from .interface import EventOccurrence
from .schema import nonempty_string, object_config


class SystemProcessExitedProvider:
    role = "event"
    provider = "system-process"
    capability = "exited"

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        value = object_config(
            config,
            version,
            {"pid", "start_identity", "stdout_path", "stderr_path", "status_path"},
            {"pid", "start_identity"},
        )
        pid = value["pid"]
        if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
            raise ValueError("pid must be a positive integer")
        value["start_identity"] = nonempty_string(value["start_identity"], "start_identity")
        for field in ("stdout_path", "stderr_path", "status_path"):
            if field in value:
                value[field] = nonempty_string(value[field], field)
        return value

    def observe(self, cursor: str | None = None) -> Iterable[EventOccurrence]:
        return ()
