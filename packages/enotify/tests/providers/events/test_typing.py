import json
import os
import time
import unittest
from unittest.mock import patch

from enotify.providers.events.typing import BuzzTypingLiveStream, BuzzTypingTransitionsProvider


def tick(event_id, at, author="author", channel="channel"):
    return {"id": event_id, "kind": 20002, "pubkey": author, "created_at": at,
            "tags": [["h", channel]]}


class TypingProviderTests(unittest.TestCase):
    class Stream:
        def __init__(self, rows):
            self.rows = list(rows)
            self.calls = 0

        def poll(self):
            self.calls += 1
            return self.rows

    def provider(self, rows, now=100, community_output=None):
        class Result:
            stdout = json.dumps(rows)
        def run(command, **kwargs):
            result = Result()
            result.stdout = json.dumps(community_output if community_output is not None else {"community": "community"}) if "channels" in command else json.dumps(rows)
            return result
        return BuzzTypingTransitionsProvider(
            run,
            {"community": "community", "channel": "channel", "author": "author"},
            lambda: now,
            self.Stream(rows),
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

    def test_community_identity_from_explicit_cli_field(self):
        provider = self.provider([], community_output={"community": "community"})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(provider.observe_ticks(), [])

    def test_community_identity_falls_back_to_environment(self):
        provider = self.provider([], community_output={})
        with patch.dict(os.environ, {"BUZZ_COMMUNITY_ID": "community"}, clear=True):
            self.assertEqual(provider.observe_ticks(), [])

    def test_community_identity_requires_cli_or_environment(self):
        provider = self.provider([], community_output={})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "BUZZ_COMMUNITY_ID is required"):
                provider.observe_ticks()

    def test_community_identity_rejects_mismatch(self):
        provider = self.provider([], community_output={"community": "other"})
        with patch.dict(os.environ, {"BUZZ_COMMUNITY_ID": "community"}, clear=True):
            with self.assertRaisesRegex(ValueError, "does not match configured community"):
                provider.observe_ticks()

    def test_live_stream_command_uses_ephemeral_event_subscription(self):
        stream = BuzzTypingLiveStream.__new__(BuzzTypingLiveStream)
        stream.community, stream.channel, stream.author = "community", "channel", "author"
        command = stream.command
        self.assertEqual(command[:5], ["buzz-server", "events", "subscribe", "--community", "community"])
        self.assertNotIn("messages", command)
        self.assertEqual(json.loads(command[-1]), {"kinds": [20002], "authors": ["author"], "#h": ["channel"]})

    def test_provider_reads_only_injected_live_stream_not_persisted_messages(self):
        calls = []
        provider = self.provider([tick("live", 100)])
        original = provider._runner
        def run(command, **kwargs):
            calls.append(command)
            return original(command, **kwargs)
        provider._runner = run
        self.assertEqual([row["id"] for row in provider.observe_ticks()], ["live"])
        self.assertEqual([command[1:3] for command in calls], [["channels", "get"]])

    def test_jsonl_stream_consumes_events_only_after_eose_and_ignores_controls(self):
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "r")
        children = []

        class Child:
            def __init__(self):
                self.stdout = reader
                self.terminated = False
                self.returncode = None
            def terminate(self):
                self.terminated = True
            def wait(self, timeout=None):
                self.returncode = 0
            def kill(self):
                self.returncode = -9

        def popen(command, **kwargs):
            child = Child()
            children.append((command, child))
            return child

        stream = BuzzTypingLiveStream("community", "channel", "author", popen_factory=popen)
        os.write(write_fd, b'{"type":"notice","message":"ignored"}\n')
        os.write(write_fd, b'not json\n')
        os.write(write_fd, (json.dumps({"type": "event", "event": tick("live", 100)}) + "\n").encode())
        self.assertEqual(stream.poll(), [])
        os.write(write_fd, b'{"type":"eose"}\n')
        rows = []
        for _ in range(20):
            rows = stream.poll()
            if rows:
                break
            time.sleep(0.01)
        self.assertEqual([row["id"] for row in rows], ["live"])
        os.close(write_fd)
        stream.close()
        self.assertTrue(children[0][1].terminated)


if __name__ == "__main__":
    unittest.main()
