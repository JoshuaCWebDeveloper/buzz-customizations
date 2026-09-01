import tempfile
import unittest
from pathlib import Path

from deploy import MARKER, UNIT_NAME, deploy, install, rollback_release, stage_release, unit, uninstall


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
            self.assertTrue((release / "enotify.py").exists())
            self.assertEqual((release / "enotify").stat().st_mode & 0o777, 0o755)
            self.assertEqual((release / "migrations").stat().st_mode & 0o777, 0o755)
            self.assertIn(str(release / "enotify-worker.py"), unit(state, release))
            self.assertIn(f"ENOTIFY_DB={state / 'enotify.db'}", unit(state, release))
            self.assertNotIn("BUZZ_PRIVATE_KEY", unit(state, release))

    def test_release_rollback_restores_previous_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / "release"
            backup = Path(directory) / "release.previous"
            release.mkdir()
            backup.mkdir()
            (release / "version").write_text("new", encoding="utf-8")
            (backup / "version").write_text("old", encoding="utf-8")
            rollback_release(release)
            self.assertEqual((release / "version").read_text(encoding="utf-8"), "old")
            self.assertFalse(backup.exists())

    def test_install_starts_new_unit_but_restarts_active_unit(self):
        for active, expected in ((False, "start"), (True, "restart")):
            with self.subTest(active=active), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                calls = []
                class Result:
                    def __init__(self, returncode=0):
                        self.returncode = returncode
                def runner(command, **kwargs):
                    calls.append(command)
                    if command[:2] == ["systemctl", "is-active"]:
                        return Result(0 if active else 3)
                    return Result(0)
                deploy("install", root / "state", root / "units", runner=runner, release_dir=root / "release")
                self.assertIn(["systemctl", "is-active", UNIT_NAME], calls)
                self.assertIn(["systemctl", expected, UNIT_NAME], calls)
                if active:
                    self.assertNotIn(["systemctl", "start", UNIT_NAME], calls)
                else:
                    self.assertNotIn(["systemctl", "restart", UNIT_NAME], calls)
