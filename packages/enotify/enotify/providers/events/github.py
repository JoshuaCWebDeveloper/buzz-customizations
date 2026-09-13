from __future__ import annotations

import re
import json
from urllib.request import Request, urlopen
from typing import Any, Callable, Iterable
from ...credentials import CredentialReference, CredentialResolver
from .interface import EventOccurrence
from .schema import nonempty_string, object_config, predicate

_REPOSITORY = re.compile(r"[^/\s]+/[^/\s]+\Z")


class GitHubCheckProvider:
    role = "event"
    provider = "github"
    capability = "check"

    def __init__(self, fetch: Callable[[str], Any] | None = None, config: dict[str, Any] | None = None,
                 credential_resolver: CredentialResolver | None = None):
        self._fetch = fetch or self._request
        self.config = dict(config or {})
        self._credential_resolver = credential_resolver or CredentialResolver()

    def describe(self) -> dict[str, Any]:
        return {"role": self.role, "provider": self.provider, "capabilities": [self.capability], "schema_versions": [1]}

    def validate_config(self, config: dict[str, Any], version: int) -> dict[str, Any]:
        value = object_config(
            config, version, {"repository", "check", "pull_request", "credential_ref"}, {"repository", "check"}
        )
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
        reference = value.get("credential_ref", {"scope": "service", "name": "github"})
        value["credential_ref"] = CredentialResolver.validate(
            CredentialReference.from_mapping(reference), ("github",)
        ).mapping()
        value["repository"] = repository
        value["check"] = normalized_check
        return value

    def observe(self, cursor: str | None = None) -> Iterable[EventOccurrence]:
        repository = self.config.get("repository")
        if not repository:
            return ()
        pull_number = (self.config.get("pull_request") or {}).get("number")
        if pull_number is not None:
            pull = self._fetch(f"https://api.github.com/repos/{repository}/pulls/{pull_number}")
            head = pull.get("head") if isinstance(pull, dict) else None
            head_sha = head.get("sha") if isinstance(head, dict) else None
            if not isinstance(head_sha, str) or not head_sha:
                raise ValueError("GitHub pull request response missing head.sha")
            commits = [{"sha": head_sha}]
        else:
            commits = self._fetch(f"https://api.github.com/repos/{repository}/commits?per_page=20")
        if not isinstance(commits, list):
            raise ValueError("GitHub commits response must be an array")
        wanted = self.config.get("check", {})
        result: list[EventOccurrence] = []
        for commit in commits:
            sha = commit.get("sha") if isinstance(commit, dict) else None
            if not isinstance(sha, str):
                continue
            runs = self._fetch(f"https://api.github.com/repos/{repository}/commits/{sha}/check-runs?per_page=100")
            for run in runs.get("check_runs", []) if isinstance(runs, dict) else []:
                if not isinstance(run, dict) or not self._matches(run, wanted, None):
                    continue
                stamp = str(run.get("updated_at") or run.get("completed_at") or run.get("started_at") or "")
                if cursor and stamp and stamp <= cursor:
                    continue
                # A check-run ID is stable across state changes; include the
                # normalized transition fields so queued/in-progress/completed
                # are separate occurrences while poll/webhook replay dedupes.
                run_id = run.get("id") or f"{sha}:{run.get('name')}"
                transition = json.dumps(
                    {"status": run.get("status"), "conclusion": run.get("conclusion"), "updated_at": stamp},
                    sort_keys=True, separators=(",", ":")
                )
                identity = f"{run_id}:{transition}"
                payload = dict(run)
                payload.setdefault("head_sha", sha)
                result.append(EventOccurrence(self.provider, repository, identity, str(stamp), str(stamp), payload))
        return sorted(result, key=lambda occurrence: occurrence.observed_at)

    @staticmethod
    def _matches(run: dict[str, Any], wanted: dict[str, Any], pull_number: int | None) -> bool:
        values = {
            "name": run.get("name"), "app": (run.get("app") or {}).get("slug") if isinstance(run.get("app"), dict) else run.get("app"),
            "status": run.get("status"), "conclusion": run.get("conclusion"),
        }
        for key, predicate_value in wanted.items():
            actual = values.get(key)
            if "equals" in predicate_value and actual != predicate_value["equals"]:
                return False
            if "in" in predicate_value and actual not in predicate_value["in"]:
                return False
        if pull_number is not None:
            prs = run.get("pull_requests")
            if not isinstance(prs, list) or not any(isinstance(pr, dict) and pr.get("number") == pull_number for pr in prs):
                return False
        return True

    def _request(self, url: str) -> Any:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "enotify"}
        reference = CredentialResolver.validate(
            CredentialReference.from_mapping(
                self.config.get("credential_ref", {"scope": "service", "name": "github"})
            ),
            ("github",),
        )
        token = self._credential_resolver.resolve(reference, ("GITHUB_TOKEN", "GH_TOKEN"))
        headers["Authorization"] = f"Bearer {token}"
        request = Request(url, headers=headers)
        with urlopen(request, timeout=20) as response:
            return json.load(response)
