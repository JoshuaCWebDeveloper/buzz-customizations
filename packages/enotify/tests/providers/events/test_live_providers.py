import json
import tempfile
import unittest
from pathlib import Path

from enotify.providers.events.buzz import BuzzChannelEventsProvider
from enotify.providers.events.github import GitHubCheckProvider
from enotify.providers.events.system_process import SystemProcessExitedProvider


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

    def test_github_matches_nested_check_and_pr(self):
        responses = {
            "commits": [{"sha": "abc"}],
            "runs": {"check_runs": [{"id": 7, "name": "ci", "status": "completed", "conclusion": "success", "pull_requests": [{"number": 4}]}]},
        }
        def fetch(url):
            return responses["commits"] if "/commits?" in url else responses["runs"]
        provider = GitHubCheckProvider(fetch, {"repository": "o/r", "check": {"name": {"equals": "ci"}}, "pull_request": {"number": 4}})
        self.assertTrue(list(provider.observe())[0].occurrence_id.startswith("7:"))

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
