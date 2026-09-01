import unittest

from enotify.providers.events import EventRegistry, default_registry
from enotify.providers.events.buzz import BuzzChannelEventsProvider
from enotify.providers.notifications.buzz import BuzzMessageProvider


class EventProviderTests(unittest.TestCase):
    def test_role_qualified_registry_and_duplicates_fail_closed(self):
        registry = default_registry()
        self.assertEqual(registry.get("buzz", "channel-events").role, "event")
        with self.assertRaises(KeyError):
            registry.get("buzz", "message")
        with self.assertRaises(ValueError):
            EventRegistry((BuzzChannelEventsProvider(), BuzzChannelEventsProvider()))
        with self.assertRaises(ValueError):
            EventRegistry((BuzzMessageProvider(),))

    def test_strict_provider_schemas_and_canonical_defaults(self):
        registry = default_registry()
        buzz = registry.get("buzz", "channel-events")
        self.assertEqual(
            buzz.validate_config(
                {"community": "community", "channel": "channel", "kind": 9}, 1
            )["kind"],
            9,
        )
        github = registry.get("github", "check")
        value = github.validate_config(
            {
                "repository": "owner/repo",
                "check": {
                    "name": {"equals": "ci"},
                    "status": {"in": ["queued", "completed", "queued"]},
                },
                "pull_request": {"number": 42},
            },
            1,
        )
        self.assertEqual(value["check"]["status"]["in"], ["completed", "queued"])
        process = registry.get("system-process", "exited")
        self.assertEqual(
            process.validate_config({"pid": 7, "start_identity": "linux:123"}, 1)["pid"],
            7,
        )
        invalid = (
            (buzz, {"community": "c"}),
            (github, {"repository": "owner/repo", "check": {"unknown": "x"}}),
            (process, {"pid": True, "start_identity": "x"}),
        )
        for provider, config in invalid:
            with self.subTest(provider=provider.provider):
                with self.assertRaises(ValueError):
                    provider.validate_config(config, 1)
