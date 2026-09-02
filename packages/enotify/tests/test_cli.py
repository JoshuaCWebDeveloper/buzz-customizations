from contextlib import redirect_stdout
from io import StringIO
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import importlib.util


def load_cli():
    path = Path(__file__).parents[1] / "enotify.py"
    spec = importlib.util.spec_from_file_location("enotify_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CliTests(unittest.TestCase):
    def test_create_defaults_to_all_and_accepts_explicit_frequencies(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            event = json.dumps({
                "provider": "buzz", "event_type": "typing-transitions",
                "match": {"community": "community", "channel": "channel", "author": "author"},
            })
            notification = json.dumps({
                "provider": "buzz", "notification_type": "message",
                "address": {"community": "community", "channel": "channel"},
            })
            def create(*extra):
                output = StringIO()
                with redirect_stdout(output):
                    self.assertEqual(cli.main([
                        "--db", str(database), "subscription", "create", *extra,
                        "--event-spec", event, "--notification-spec", notification,
                    ]), 0)
                return json.loads(output.getvalue())

            self.assertEqual(create()["frequency"], "all")
            self.assertEqual(create("--frequency", "one")["frequency"], "one")
            self.assertEqual(create("--frequency", "all")["frequency"], "all")

    def test_invalid_content_is_rejected_during_create_and_update(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "state.sqlite"
            event = root / "event.json"
            notification = root / "notification.json"
            event.write_text(json.dumps({
                "provider": "github", "event_type": "check", "schema_version": 1,
                "match": {"repository": "owner/repo", "check": {"name": {"equals": "ci"}}},
            }), encoding="utf-8")
            notification.write_text(json.dumps({
                "provider": "buzz", "notification_type": "message", "schema_version": 1,
                "address": {"community": "community", "channel": "channel", "content": "{payload.x}"},
            }), encoding="utf-8")
            self.assertEqual(cli.main([
                "--db", str(database), "subscription", "create", "--frequency", "one",
                "--event-spec", str(event), "--notification-spec", str(notification),
            ]), 2)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)

            notification.write_text(json.dumps({
                "provider": "buzz", "notification_type": "message", "schema_version": 1,
                "address": {"community": "community", "channel": "channel"},
            }), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main([
                    "--db", str(database), "subscription", "create", "--frequency", "one",
                    "--event-spec", str(event), "--notification-spec", str(notification),
                ]), 0)
            created = json.loads(output.getvalue())
            notification.write_text(json.dumps({
                "provider": "buzz", "notification_type": "message", "schema_version": 1,
                "address": {"community": "community", "channel": "channel", "content": "{payload.x}"},
            }), encoding="utf-8")
            self.assertEqual(cli.main([
                "--db", str(database), "subscription", "update", created["id"],
                "--if-revision", "1", "--notification-spec", str(notification),
            ]), 2)

    def test_inline_json_specs_apply_version_and_provider_defaults(self):
        cli = load_cli()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "state.sqlite"
            event = json.dumps({
                "provider": "buzz", "event_type": "typing-transitions",
                "match": {"community": "community", "channel": "channel", "author": "author"},
            })
            notification = json.dumps({
                "provider": "buzz", "notification_type": "message",
                "address": {"community": "community", "channel": "channel"},
            })
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(cli.main([
                    "--db", str(database), "subscription", "create", "--frequency", "one",
                    "--event-spec", event, "--notification-spec", notification,
                ]), 0)
            created = json.loads(output.getvalue())
            self.assertEqual(created["event_trigger"]["schema_version"], 1)
            self.assertEqual(created["event_trigger"]["match"]["ttl"], 8)
            self.assertEqual(created["event_trigger"]["match"]["history_limit"], 1000)
            self.assertEqual(created["notification_address"]["schema_version"], 1)
            self.assertNotIn("mention", created["notification_address"]["address"])

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
