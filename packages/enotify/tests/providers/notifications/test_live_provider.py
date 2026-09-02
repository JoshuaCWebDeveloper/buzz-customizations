import json
import unittest
from unittest.mock import patch

from enotify.providers.events import EventOccurrence
from enotify.providers.notifications.buzz import BuzzMessageProvider


class Result:
    returncode = 0
    stderr = ""
    stdout = json.dumps({"event_id": "receipt"})


class BuzzNotificationTests(unittest.TestCase):
    def occurrence(self, direction="started", author="Phaeax"):
        return EventOccurrence(
            "buzz", "typing-source", "occurrence", "100",
            payload={"author": author, "direction": direction},
        )

    def test_custom_content_renders_started_and_stopped(self):
        provider = BuzzMessageProvider({
            "community": "c", "channel": "ch",
            "content": "{author} has {direction} working",
        })
        self.assertEqual(provider.render(self.occurrence("started")), "Phaeax has started working")
        self.assertEqual(provider.render(self.occurrence("stopped")), "Phaeax has stopped working")

    def test_default_content_is_compact(self):
        provider = BuzzMessageProvider({"community": "c", "channel": "ch"})
        self.assertEqual(provider.render(self.occurrence(author="a" * 64)), "Typing started")

    def test_static_name_template_uses_real_typing_payload(self):
        provider = BuzzMessageProvider({
            "community": "c", "channel": "ch",
            "content": "Phaeax has {direction} working",
        })
        self.assertEqual(provider.render(self.occurrence("started", author="a" * 64)), "Phaeax has started working")
        self.assertEqual(provider.render(self.occurrence("stopped", author="a" * 64)), "Phaeax has stopped working")

    def test_author_template_uses_validated_mention_handle(self):
        provider = BuzzMessageProvider({
            "community": "c", "channel": "ch",
            "mention": {"pubkey": "pk", "handle": "Phaeax"},
            "content": "{author} has {direction} working",
        })
        self.assertEqual(provider.render(self.occurrence("started", author="a" * 64)), "Phaeax has started working")

    def test_content_template_is_strictly_allowlisted(self):
        provider = BuzzMessageProvider({"community": "c", "channel": "ch"})
        for template in ("{unknown}", "{author.name}", "{author!r}", "{author:>10}", "{"):
            with self.assertRaises(ValueError):
                provider.validate_config({"community": "c", "channel": "ch", "content": template}, 1)

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

    def test_mention_composes_with_rendered_content(self):
        seen = {}
        def run(command, **kwargs):
            seen.update(command=command, **kwargs)
            result = Result()
            result.stdout = json.dumps({"community": "c"}) if "channels" in command else Result.stdout
            return result
        provider = BuzzMessageProvider({
            "community": "c", "channel": "ch",
            "mention": {"pubkey": "pk", "handle": "Ada"},
            "content": "{author} has {direction} working",
        }, run)
        result = provider.send(provider.render(self.occurrence("stopped")), "key")
        self.assertEqual(result.outcome, "accepted")
        self.assertEqual(seen["input"], "@Ada Phaeax has stopped working")

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

    def test_send_uses_environment_community_when_cli_omits_it(self):
        def run(command, **kwargs):
            result = Result()
            result.stdout = json.dumps({"channel_id": "ch"}) if "channels" in command else json.dumps({"event_id": "receipt"})
            return result
        with patch.dict("os.environ", {"BUZZ_COMMUNITY_ID": "c"}):
            result = BuzzMessageProvider({"community": "c", "channel": "ch"}, run).send("hello", "key")
        self.assertEqual(result.outcome, "accepted")
