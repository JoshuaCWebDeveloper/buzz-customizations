import tempfile
import unittest
from pathlib import Path

from deploy import MARKER, install, stage_release, unit, uninstall


class DeployTests(unittest.TestCase):
    def test_undeploy_preserves_state_and_unrelated_files(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            install(state)
            database = state / "enotify.sqlite"
            unrelated = state / "operator.conf"
            database.write_text("state", encoding="utf-8")
            unrelated.write_text("keep", encoding="utf-8")
            self.assertTrue((state / MARKER).exists())
            uninstall(state)
            self.assertFalse((state / MARKER).exists())
            self.assertEqual(database.read_text(encoding="utf-8"), "state")
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")

    def test_release_is_staged_and_unit_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "release"
            state = Path(directory) / "state"
            stage_release(release)
            install(state)
            self.assertTrue((release / "enotify-worker.py").exists())
            self.assertIn(str(release / "enotify-worker.py"), unit(state, release))
            self.assertNotIn("BUZZ_PRIVATE_KEY", unit(state, release))
