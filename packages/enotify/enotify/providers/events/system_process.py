from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
from .interface import EventOccurrence
from .schema import nonempty_string, object_config


class SystemProcessExitedProvider:
    role = "event"
    provider = "system-process"
    capability = "exited"

    def __init__(self, config: dict[str, Any] | None = None, proc_root: Path = Path("/proc")):
        self.config = dict(config or {})
        self.proc_root = Path(proc_root)

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
        pid = self.config.get("pid")
        identity = self.config.get("start_identity")
        if not isinstance(pid, int) or not isinstance(identity, str):
            return ()
        stat = self.proc_root / str(pid) / "stat"
        if stat.exists():
            try:
                text = stat.read_text(encoding="utf-8")
            except OSError as exc:
                raise RuntimeError("process stat is unreadable") from exc
            closing = text.rfind(")")
            fields = text[closing + 2:].split() if closing >= 0 else []
            if len(fields) > 19 and fields[19] == identity:
                return ()
            if len(fields) > 19 and fields[19] != identity:
                return (EventOccurrence(self.provider, str(pid), f"{pid}:{identity}:pid_reused", "", f"pid_reused:{identity}", {"pid": pid, "start_identity": identity, "reason": "pid_reused"}),)
        occurrence_id = f"{pid}:{identity}"
        payload: dict[str, Any] = {"pid": pid, "start_identity": identity}
        for field in ("stdout_path", "stderr_path", "status_path"):
            path = self.config.get(field)
            if path and Path(path).is_file():
                try:
                    with Path(path).open("rb") as handle:
                        data = handle.read(1024 * 1024 + 1)
                except OSError:
                    payload[field] = {"path": path, "available": False, "error": "unreadable"}
                    continue
                limit = 1024 * 1024
                payload[field] = {"path": path, "available": True, "bytes": data[:limit].decode("utf-8", errors="replace"), "truncated": len(data) > limit}
        return (EventOccurrence(self.provider, str(pid), occurrence_id, "", occurrence_id, payload),)
