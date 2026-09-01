import tempfile
import unittest
from pathlib import Path

from deploy import MARKER, install, uninstall


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
