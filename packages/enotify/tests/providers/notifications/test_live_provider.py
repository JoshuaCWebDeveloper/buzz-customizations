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
            return Result()
        provider = BuzzMessageProvider({"community": "c", "channel": "ch", "mention": {"pubkey": "pk", "handle": "Ada"}}, run)
        result = provider.send("hello", "key")
        self.assertEqual(result.receipt, "receipt")
        self.assertEqual(seen["input"], "@Ada hello")
        self.assertIn("pk", seen["command"])

