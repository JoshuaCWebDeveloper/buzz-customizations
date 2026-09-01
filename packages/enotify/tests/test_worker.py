import tempfile
import unittest
from pathlib import Path

from enotify.providers.events import EventOccurrence
from enotify.providers.notifications import SendResult
from enotify.storage import Store
from enotify.worker import Worker
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
