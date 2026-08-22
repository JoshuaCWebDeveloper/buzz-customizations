import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).parent
DEPLOY = HERE / "deploy.py"
SOURCE = HERE / "base_prompt.md"


class BasePromptTests(unittest.TestCase):
    def test_deploy_copies_prompt_bytes_to_overridden_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "nested" / "base_prompt.md"
            result = subprocess.run(
                [sys.executable, str(DEPLOY), "install", "--destination", str(destination)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(destination.read_bytes(), SOURCE.read_bytes())
            self.assertEqual(
                hashlib.sha256(destination.read_bytes()).hexdigest(),
                hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            )
            result = subprocess.run(
                [sys.executable, str(DEPLOY), "uninstall", "--destination", str(destination)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
