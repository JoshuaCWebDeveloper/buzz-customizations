"""Reference-only credential resolution for long-running providers."""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
from collections.abc import Mapping


_NAME = re.compile(r"[a-z][a-z0-9_.-]*\Z")


@dataclass(frozen=True)
class CredentialReference:
    """A persisted pointer to a credential, never the credential itself."""

    scope: str
    name: str

    @classmethod
    def from_mapping(cls, value: object) -> "CredentialReference":
        if not isinstance(value, Mapping) or set(value) != {"scope", "name"}:
            raise ValueError("credential_ref must contain only scope and name")
        scope = value["scope"]
        name = value["name"]
        if scope != "service":
            raise ValueError("credential_ref.scope must be service")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("credential_ref.name must be a safe reference name")
        return cls(scope, name)

    def mapping(self) -> dict[str, str]:
        return {"scope": self.scope, "name": self.name}


class CredentialUnavailable(RuntimeError):
    """Raised without exposing credential values or environment contents."""


class CredentialResolver:
    """Resolve service references from the process environment.

    The environment is the deployment-owned boundary today. The resolver is
    intentionally injected with candidate variable names so a future user or
    secret-store scope can be added without changing subscription schemas.
    """

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = os.environ if environ is None else environ

    @staticmethod
    def validate(reference: CredentialReference, allowed_names: tuple[str, ...]) -> CredentialReference:
        if reference.name not in allowed_names:
            allowed = ", ".join(allowed_names)
            raise ValueError(f"unsupported credential reference; allowed service references: {allowed}")
        return reference

    def resolve(self, reference: CredentialReference, environment_names: tuple[str, ...]) -> str:
        if reference.scope != "service":
            raise CredentialUnavailable(f"credential reference scope unsupported: {reference.scope}")
        if not reference.name:
            raise CredentialUnavailable("credential reference name is empty")
        for environment_name in environment_names:
            value = self.environ.get(environment_name)
            if value:
                return value
        names = ", ".join(environment_names)
        raise CredentialUnavailable(
            f"service credential unavailable for reference '{reference.name}' (checked {names})"
        )
