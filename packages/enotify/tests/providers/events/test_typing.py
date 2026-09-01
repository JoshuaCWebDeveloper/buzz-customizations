import json
import unittest

from enotify.providers.events.typing import BuzzTypingTransitionsProvider


def tick(event_id, at, author="author", channel="channel"):
    return {"id": event_id, "kind": 20002, "pubkey": author, "created_at": at,
            "tags": [["h", channel]]}


class TypingProviderTests(unittest.TestCase):
    def provider(self, rows, now=100):
        class Result:
            stdout = json.dumps(rows)
        def run(command, **kwargs):
            result = Result()
            result.stdout = json.dumps({"community": "community"}) if "channels" in command else json.dumps(rows)
            return result
        return BuzzTypingTransitionsProvider(
            run,
            {"community": "community", "channel": "channel", "author": "author"},
            lambda: now,
        )

    def test_default_and_strict_ttl(self):
        provider = self.provider([])
        self.assertEqual(provider.config["ttl"], 8)
        with self.assertRaises(ValueError):
            provider.validate_config({"community": "c", "channel": "h", "author": "a", "ttl": True}, 1)

    def test_start_refresh_and_due_stop(self):
        provider = self.provider([tick("a", 100), tick("b", 103)])
        self.assertEqual(len(provider.observe(observed_at=103)), 2)
        self.assertIsNone(provider.next_due())
        self.assertEqual(provider.transition_occurrence("started", 100, 103).payload["direction"], "started")

    def test_due_first_and_delayed_tick_does_not_restart(self):
        provider = self.provider([tick("a", 100), tick("b", 100)])
        self.assertEqual(len(tuple(provider.observe(observed_at=100))), 2)

    def test_unrelated_malformed_equal_and_out_of_order_are_noops(self):
        rows = [tick("bad", 99, author="other"), {"id": "malformed", "kind": 20002}, tick("a", 100), tick("b", 100), tick("c", 99)]
        provider = self.provider(rows)
        result = list(provider.observe(observed_at=100))
        self.assertEqual([item.occurrence_id for item in result], ["c", "a", "b"])

    def test_ttl_is_source_identity(self):
        first = self.provider([])
        second = self.provider([])
        second.config["ttl"] = 9
        self.assertNotEqual(first.source, second.source)


if __name__ == "__main__":
    unittest.main()
