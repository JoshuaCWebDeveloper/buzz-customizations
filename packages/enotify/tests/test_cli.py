from contextlib import redirect_stdout
from io import StringIO
import json
import tempfile
import unittest
from pathlib import Path

import enotify as domain
from enotify import models
from enotify.storage import Store
import importlib.util


def load_cli():
    path = Path(__file__).parents[1] / "enotify.py"
    spec = importlib.util.spec_from_file_location("enotify_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CliTests(unittest.TestCase):
    def test_provider_inspection_and_json_file_crud(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite"
            event = root / "event.json"
            notification = root / "notification.json"
            event.write_text(
                json.dumps(
                    {
                        "provider": "github",
                        "event_type": "check",
                        "schema_version": 1,
                        "match": {
                            "repository": "owner/repo",
                            "check": {"name": {"equals": "ci"}},
                        },
                    }
                ),
                encoding="utf-8",
            )
            notification.write_text(
                json.dumps(
                    {
                        "provider": "buzz",
                        "notification_type": "message",
                        "schema_version": 1,
                        "address": {"community": "community", "channel": "channel"},
                    }
                ),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    cli.main(
                        [
                            "--db",
                            str(database),
                            "subscription",
                            "create",
                            "--frequency",
                            "one",
                            "--event-spec",
                            str(event),
                            "--notification-spec",
                            str(notification),
                        ]
                    ),
                    0,
                )
            created = json.loads(output.getvalue())
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    cli.main(
                        [
                            "--db",
                            str(database),
                            "subscription",
                            "pause",
                            created["id"],
                            "--if-revision",
                            "1",
                        ]
                    ),
                    0,
                )
            self.assertEqual(json.loads(output.getvalue())["state"], "paused")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    cli.main(["provider", "describe", "event", "github", "check"]),
                    0,
                )
            self.assertEqual(json.loads(output.getvalue())["provider"], "github")
