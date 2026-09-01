import json
import unittest

from enotify.providers.notifications.buzz import BuzzMessageProvider


class Result:
    returncode = 0
    stderr = ""
    stdout = json.dumps({"event_id": "receipt"})


class BuzzNotificationTests(unittest.TestCase):
    def test_send_uses_stdin_and_explicit_mention(self):
        seen = {}
        def run(command, **kwargs):
            seen["command"] = command
            seen.update(kwargs)
            if "channels" in command:
                result = Result()
                result.stdout = json.dumps({"community": "c"})
                return result
            return Result()
        provider = BuzzMessageProvider({"community": "c", "channel": "ch", "mention": {"pubkey": "pk", "handle": "Ada"}}, run)
        result = provider.send("hello", "key")
        self.assertEqual(result.receipt, "receipt")
        self.assertEqual(seen["input"], "@Ada hello")
        self.assertIn("pk", seen["command"])

    def test_omitted_mention_has_no_mention_argument_or_text(self):
        seen = {}
        def run(command, **kwargs):
            seen.update(command=command, **kwargs)
            result = Result()
            result.stdout = json.dumps({"community": "c"}) if "channels" in command else json.dumps({"event_id": "receipt"})
            return result
        result = BuzzMessageProvider({"community": "c", "channel": "ch"}, run).send("hello", "key")
        self.assertEqual(result.outcome, "accepted")
        self.assertNotIn("--mention", seen["command"])
        self.assertEqual(seen["input"], "hello")

    def test_missing_receipt_fails_closed(self):
        def run(command, **kwargs):
            result = Result()
            result.stdout = json.dumps({"community": "c"}) if "channels" in command else "{}"
            return result
        result = BuzzMessageProvider({"community": "c", "channel": "ch"}, run).send("hello", "key")
        self.assertEqual(result.outcome, "permanent")
