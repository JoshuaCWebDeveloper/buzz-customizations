import unittest

from enotify.providers.events.buzz import BuzzChannelEventsProvider
from enotify.providers.notifications import NotificationRegistry, default_registry
from enotify.providers.notifications.buzz import BuzzMessageProvider


class NotificationProviderTests(unittest.TestCase):
    def test_buzz_can_coexist_only_in_notification_role(self):
        registry = default_registry()
        provider = registry.get("buzz", "message")
        self.assertEqual(provider.role, "notification")
        with self.assertRaises(KeyError):
            registry.get("buzz", "channel-events")
        with self.assertRaises(ValueError):
            NotificationRegistry((BuzzMessageProvider(), BuzzMessageProvider()))
        with self.assertRaises(ValueError):
            NotificationRegistry((BuzzChannelEventsProvider(),))

    def test_address_pins_structured_mention(self):
        provider = default_registry().get("buzz", "message")
        value = provider.validate_config(
            {
                "community": "community",
                "channel": "channel",
                "mention": {"pubkey": "abc123", "handle": "Alice"},
            },
            1,
        )
        self.assertEqual(value["mention"]["pubkey"], "abc123")
        with self.assertRaises(ValueError):
            provider.validate_config(
                {"community": "community", "channel": "channel", "mention": "Alice"},
                1,
            )
