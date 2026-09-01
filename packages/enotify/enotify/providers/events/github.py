from __future__ import annotations

import re
from typing import Any, Iterable
from .interface import EventOccurrence
from .schema import nonempty_string, object_config, predicate

_REPOSITORY = re.compile(r"[^/\s]+/[^/\s]+\Z")


class GitHubCheckProvider:
    role = "event"
    provider = "github"
    capability = "check"

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        value = object_config(config, version, {"repository", "check", "pull_request"}, {"repository", "check"})
        repository = nonempty_string(value["repository"], "repository")
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("repository must be owner/name")
        check = value["check"]
        if not isinstance(check, dict) or not check:
            raise ValueError("check must be a non-empty object")
        unknown = set(check) - {"name", "app", "status", "conclusion"}
        if unknown:
            raise ValueError("unknown check fields: " + ",".join(sorted(unknown)))
        normalized_check = {key: predicate(item, f"check.{key}") for key, item in check.items()}
        if "pull_request" in value:
            pull_request = value["pull_request"]
            if not isinstance(pull_request, dict) or set(pull_request) != {"number"}:
                raise ValueError("pull_request must contain only number")
            number = pull_request["number"]
            if not isinstance(number, int) or isinstance(number, bool) or number < 1:
                raise ValueError("pull_request.number must be a positive integer")
            value["pull_request"] = {"number": number}
        value["repository"] = repository
        value["check"] = normalized_check
        return value

    def observe(self, cursor: str | None = None) -> Iterable[EventOccurrence]:
        return ()
