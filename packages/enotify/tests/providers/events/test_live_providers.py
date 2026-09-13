import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from enotify.providers.events.buzz import BuzzChannelEventsProvider
from enotify.providers.events.github import GitHubCheckProvider
from enotify.providers.events.system_process import SystemProcessExitedProvider
from enotify.credentials import CredentialResolver


class Result:
    returncode = 0
    stderr = ""


class LiveProviderTests(unittest.TestCase):
    def test_buzz_filters_and_overlaps_cursor(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            result = Result()
            result.stdout = json.dumps({"community": "c"} if "channels" in command else [{"id": "a", "created_at": 10, "pubkey": "author", "kind": 1}])
            return result
        provider = BuzzChannelEventsProvider(run, {"community": "c", "channel": "ch", "author": "author", "kind": 1})
        self.assertEqual(list(provider.observe("10"))[0].occurrence_id, "a")
        self.assertEqual(calls[1][-4:], ["--kinds", "1", "--since", "9"])

    def test_buzz_uses_configured_community_when_cli_omits_it(self):
        def run(command, **kwargs):
            result = Result()
            result.stdout = json.dumps({"channel_id": "ch"} if "channels" in command else [])
            return result
        provider = BuzzChannelEventsProvider(run, {"community": "c", "channel": "ch"})
        with patch.dict("os.environ", {"BUZZ_COMMUNITY_ID": "c"}):
            self.assertEqual(tuple(provider.observe()), ())

    def test_github_matches_nested_check_and_pr(self):
        responses = {
            "pull": {"head": {"sha": "abc"}},
            "runs": {"check_runs": [{"id": 7, "name": "ci", "status": "completed", "conclusion": "success", "pull_requests": [{"number": 4}]}]},
        }
        def fetch(url):
            return responses["pull"] if "/pulls/4" in url else responses["runs"]
        provider = GitHubCheckProvider(fetch, {"repository": "o/r", "check": {"name": {"equals": "ci"}}, "pull_request": {"number": 4}})
        self.assertTrue(list(provider.observe())[0].occurrence_id.startswith("7:"))

    def test_github_pr_scope_discovers_current_head_directly(self):
        calls = []
        responses = {
            "/pulls/4": {"state": "open", "head": {"sha": "current-head"}},
            "/commits/current-head/check-runs": {
                "check_runs": [{"id": 8, "name": "ci", "status": "in_progress", "conclusion": None,
                                 "updated_at": "2026-01-01T00:00:03Z"}]
            },
        }

        def fetch(url):
            calls.append(url)
            for suffix, value in responses.items():
                if suffix in url:
                    return value
            raise AssertionError("unexpected GitHub URL")

        provider = GitHubCheckProvider(fetch, {
            "repository": "o/r", "check": {"name": {"equals": "ci"}},
            "pull_request": {"number": 4},
        })
        occurrences = list(provider.observe())
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].payload["head_sha"], "current-head")
        self.assertNotIn("/commits?", " ".join(calls))
        self.assertIn("/pulls/4", calls[0])
        self.assertIn("/commits/current-head/check-runs", calls[1])

    def test_github_pr_scope_does_not_require_run_pull_request_metadata(self):
        def fetch(url):
            if "/pulls/4" in url:
                return {"head": {"sha": "current-head"}}
            return {"check_runs": [{"id": 9, "name": "ci", "status": "completed", "conclusion": "success",
                                     "updated_at": "2026-01-01T00:00:04Z"}]}

        provider = GitHubCheckProvider(fetch, {
            "repository": "o/r", "check": {"name": {"equals": "ci"}},
            "pull_request": {"number": 4},
        })
        self.assertEqual(len(list(provider.observe())), 1)

    def test_github_request_uses_resolved_reference_without_persisting_token(self):
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return None
            def read(self):
                return b"{}"

        resolver = CredentialResolver({"GITHUB_TOKEN": "unit-value"})
        provider = GitHubCheckProvider(config={"repository": "o/r", "check": {"name": {"equals": "ci"}}},
                                       credential_resolver=resolver)
        with patch("enotify.providers.events.github.urlopen", return_value=Response()) as open_url:
            provider._request("https://api.github.com/repos/o/r/pulls/4")
        request = open_url.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer unit-value")
        self.assertNotIn("unit-value", provider.config)

    def test_github_transition_identity_and_cursor(self):
        runs = [{"id": 7, "name": "ci", "status": "queued", "conclusion": None, "updated_at": "2026-01-01T00:00:01Z"}]
        provider = GitHubCheckProvider(lambda url: [{"sha": "a"}] if "/commits?" in url else {"check_runs": runs}, {"repository": "o/r", "check": {"name": {"equals": "ci"}}})
        first = list(provider.observe())
        runs[0].update(status="completed", conclusion="success", updated_at="2026-01-01T00:00:02Z")
        second = list(provider.observe(first[0].observed_at))
        self.assertNotEqual(first[0].occurrence_id, second[0].occurrence_id)

    def test_process_exit_reads_only_configured_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "9").mkdir()
            (root / "9" / "stat").write_text(" ".join(["9", "(worker)", "S"] + ["1"] * 18 + ["44"]), encoding="utf-8")
            output = root / "out"
            output.write_text("done", encoding="utf-8")
            provider = SystemProcessExitedProvider({"pid": 9, "start_identity": "44", "stdout_path": str(output)}, root)
            self.assertEqual(tuple(provider.observe()), ())
            (root / "9" / "stat").unlink()
            occurrence = tuple(provider.observe())[0]
            self.assertEqual(occurrence.payload["stdout_path"]["bytes"], "done")

    def test_process_artifact_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out"
            output.write_bytes(b"x" * (1024 * 1024 + 10))
            provider = SystemProcessExitedProvider({"pid": 3, "start_identity": "1", "stdout_path": str(output)}, root)
            item = tuple(provider.observe())[0].payload["stdout_path"]
            self.assertTrue(item["truncated"])
