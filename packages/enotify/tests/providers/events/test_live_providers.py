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
            result.stdout = json.dumps([{"id": "a", "created_at": 10, "pubkey": "author", "kind": 1}])
            return result
        provider = BuzzChannelEventsProvider(run, {"community": "c", "channel": "ch", "author": "author", "kind": 1})
        self.assertEqual(list(provider.observe("10"))[0].occurrence_id, "a")
        self.assertEqual(calls[0][-2:], ["--since", "9"])

    def test_github_matches_nested_check_and_pr(self):
        responses = {
            "commits": [{"sha": "abc"}],
            "runs": {"check_runs": [{"id": 7, "name": "ci", "status": "completed", "conclusion": "success", "pull_requests": [{"number": 4}]}]},
        }
        def fetch(url):
            return responses["commits"] if "/commits?" in url else responses["runs"]
        provider = GitHubCheckProvider(fetch, {"repository": "o/r", "check": {"name": {"equals": "ci"}}, "pull_request": {"number": 4}})
        self.assertEqual(list(provider.observe())[0].occurrence_id, "7")

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
            self.assertEqual(occurrence.payload["stdout_path"], "done")
