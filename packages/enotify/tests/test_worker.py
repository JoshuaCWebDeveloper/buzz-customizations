import json
import tempfile
import unittest
from pathlib import Path

from enotify.providers.events import EventOccurrence
from enotify.providers.notifications import SendResult
from enotify.storage import Store
from enotify.worker import Worker
from enotify.providers.events.typing import BuzzTypingTransitionsProvider
from tests.helpers import specs


class FakeEvents:
    provider = "fake"
    capability = "fixture"
    role = "event"

    def __init__(self, *ids):
        self.ids = ids

    def observe(self, cursor=None):
        return [
            EventOccurrence("fake", "source", item, "2026-09-01T00:00:00Z")
            for item in self.ids
        ]


class FakeNotifications:
    provider = "fake"
    capability = "fixture"
    role = "notification"

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.keys = []

    def send(self, message, delivery_key):
        self.keys.append(delivery_key)
        return self.outcomes.pop(0)


class WorkerTests(unittest.TestCase):
    def open(self, directory):
        store = Store(Path(directory) / "enotify.sqlite")
        store.open()
        return store

    def test_one_retries_selected_occurrence_then_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open(directory)
            event, notification = specs()
            subscription = store.create("one", event, notification)
            sender = FakeNotifications(
                [SendResult.retryable("timeout"), SendResult.accepted("receipt")]
            )
            Worker(store, FakeEvents("first", "later"), sender).process(
                subscription, lambda occurrence: occurrence.occurrence_id
            )
            self.assertEqual(store.get(subscription["id"])["state"], "finished")
            self.assertEqual(len(sender.keys), 2)
            self.assertEqual(sender.keys[0], sender.keys[1])
            self.assertEqual(store.status()["open_reservations"], 0)
            store.close()

    def test_one_exhaustion_fails_closed_and_release_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open(directory)
            event, notification = specs()
            subscription = store.create("one", event, notification)
            sender = FakeNotifications(
                [SendResult.retryable("one"), SendResult.retryable("two")]
            )
            Worker(
                store, FakeEvents("selected", "must-not-send"), sender, max_attempts=2
            ).process(subscription, lambda occurrence: occurrence.occurrence_id)
            paused = store.get(subscription["id"])
            self.assertEqual(paused["state"], "paused")
            self.assertEqual(len(sender.keys), 2)
            reservation = store.db.execute(
                "SELECT id,state FROM delivery_reservations WHERE subscription_id=?",
                (subscription["id"],),
            ).fetchone()
            self.assertEqual(reservation["state"], "exhausted")
            released = store.release(
                subscription["id"], reservation["id"], paused["revision"], resume=True
            )
            self.assertEqual(released["state"], "active")
            store.close()

    def test_all_dead_letters_failure_and_continues(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open(directory)
            event, notification = specs()
            subscription = store.create("all", event, notification)
            sender = FakeNotifications(
                [SendResult.permanent("bad address"), SendResult.accepted("receipt")]
            )
            Worker(store, FakeEvents("bad", "good"), sender).process(
                subscription, lambda occurrence: occurrence.occurrence_id
            )
            self.assertEqual(store.get(subscription["id"])["state"], "active")
            self.assertEqual(len(sender.keys), 2)
            self.assertEqual(store.status()["dead_letters"], 1)
            self.assertEqual(
                store.db.execute("SELECT COUNT(*) FROM accepted_deliveries").fetchone()[0],
                1,
            )
            store.close()

    def test_typing_refresh_is_durable_and_expiry_needs_no_relay_event(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open(directory)
            event, notification = specs()
            event = event.__class__("buzz", "typing-transitions", 1, {"community": "c", "channel": "ch", "author": "a", "ttl": 8})
            subscription = store.create("all", event, notification)
            rows = [{"id": "tick-1", "kind": 20002, "pubkey": "a", "created_at": 100, "tags": [["h", "ch"]]}]
            class Result:
                stdout = json.dumps({"community": "c"} if False else rows)
            def run(command, **kwargs):
                result = Result()
                result.stdout = json.dumps({"community": "c"}) if "channels" in command else json.dumps(rows)
                return result
            provider = BuzzTypingTransitionsProvider(run, dict(event.match), lambda: 100)
            sender = FakeNotifications([SendResult.accepted("start")])
            Worker(store, provider, sender, clock=lambda: 100).process(subscription, lambda occurrence: occurrence.payload["direction"])
            self.assertEqual(store.typing_projection("buzz", provider.source)["expires_at"], 108)
            restarted = BuzzTypingTransitionsProvider(run, dict(event.match), lambda: 108)
            sender2 = FakeNotifications([SendResult.accepted("stop")])
            Worker(store, restarted, sender2, clock=lambda: 108).process(subscription, lambda occurrence: occurrence.payload["direction"])
            self.assertEqual(sender2.keys, [sender2.keys[0]])
            self.assertEqual(store.typing_projection("buzz", provider.source)["active"], 0)
            store.close()
