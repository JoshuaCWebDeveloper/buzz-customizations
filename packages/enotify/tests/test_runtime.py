import ast
from pathlib import Path
import tempfile
import unittest

from enotify.models import EventTriggerSpec, NotificationAddressSpec
from enotify.providers.events.interface import EventOccurrence
from enotify.providers.notifications import SendResult
from enotify.runtime import RuntimeRegistry, WakeCoordinator
from enotify.storage import Store
from enotify.worker import Worker


class FakeProvider:
    provider = "fake"
    capability = "stateful"
    role = "event"

    def __init__(self, source="fake-source"):
        self.source = source


class FakeExtension:
    provider = "fake"
    capability = "stateful"

    def __init__(self):
        self.actions = []

    def on_create(self, transaction, subscription_id, event, stamp):
        self.actions.append(("create", subscription_id))

    def on_update(self, transaction, old, event, revision):
        self.actions.append(("update", old["id"]))

    def on_transition(self, transaction, subscription, action, revision):
        self.actions.append((action, subscription["id"]))


class FakeBackend:
    created = 0
    stopped = 0

    def __init__(self, provider, store, wake, **_):
        type(self).created += 1
        self.provider = provider
        self.store = store
        self.wake = wake
        self.used = False

    def start(self):
        self.wake()

    def bind(self, subscription, **_):
        return FakeHandle(self, subscription)

    def stop(self):
        type(self).stopped += 1

    def next_deadline(self, now):
        return now + 5

    def health(self):
        return {"provider": "fake", "ready": True, "error": None}


class FakeHandle:
    def __init__(self, backend, subscription):
        self.backend = backend
        self.subscription = subscription
        self.provider = backend.provider.provider
        self.source = backend.provider.source

    def start(self):
        return None

    def observe(self, cursor, observed_at):
        self.backend.used = True
        occurrence = EventOccurrence("fake", self.source, "fake-occurrence", str(observed_at), "1", {"state": "ready"})
        self.backend.store.record_occurrence(occurrence)
        return (occurrence,)

    def advance(self, observed_at):
        return ()

    def next_deadline(self, observed_at):
        return observed_at + 5

    def health(self):
        return {"provider": "fake", "source": self.source, "ready": True, "error": None}

    def ack(self, occurrence):
        return None

    def stop(self):
        return None


class FakeNotifications:
    def send(self, message, delivery_key):
        return SendResult.accepted("fake-receipt")


class RuntimeTests(unittest.TestCase):
    def open_store(self, directory, extension=None):
        return Store(Path(directory) / "state.sqlite", extensions={("fake", "stateful"): extension} if extension else {})

    def specs(self):
        return (
            EventTriggerSpec("fake", "stateful", 1, {"source": "fake-source"}),
            NotificationAddressSpec("buzz", "message", 1, {"community": "c", "channel": "ch"}),
        )

    def test_fake_stateful_provider_registration_shared_lifetime_and_worker_delivery(self):
        FakeBackend.created = FakeBackend.stopped = 0
        with tempfile.TemporaryDirectory() as directory:
            extension = FakeExtension()
            store = self.open_store(directory, extension)
            store.open()
            event, notification = self.specs()
            first = store.create("all", event, notification)
            second = store.create("all", event, notification.__class__("buzz", "message", 1, {"community": "c", "channel": "other"}))
            provider = FakeProvider()
            registry = RuntimeRegistry(WakeCoordinator())
            registry.register("fake", "stateful", FakeBackend)
            first_handle = registry.bind(provider, store=store, subscription=first)
            second_handle = registry.bind(FakeProvider(), store=store, subscription=second)
            self.assertEqual(FakeBackend.created, 1)
            self.assertEqual(registry.deadlines(100), [105])
            Worker(store, first_handle, FakeNotifications()).process(first, lambda occurrence: "ready")
            Worker(store, second_handle, FakeNotifications()).process(second, lambda occurrence: "ready")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM delivery_reservations").fetchone()[0], 2)
            first_handle.stop()
            self.assertEqual(FakeBackend.stopped, 0)
            second_handle.stop()
            self.assertEqual(FakeBackend.stopped, 1)
            self.assertEqual([action[0] for action in extension.actions], ["create", "create"])
            store.close()

    def test_provider_transaction_rolls_back_common_occurrence_and_recovers_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open_store(directory)
            store.open()
            occurrence = EventOccurrence("fake", "source", "rollback", "1", "1", {})
            with self.assertRaisesRegex(RuntimeError, "rollback"):
                with store.transaction() as transaction:
                    transaction.record_occurrence(occurrence)
                    raise RuntimeError("rollback")
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM event_occurrences").fetchone()[0], 0)
            store.record_occurrence(occurrence)
            store.close()
            restarted = self.open_store(directory)
            restarted.open()
            self.assertEqual(restarted.checkpoint("fake", "source"), "1")
            self.assertEqual(restarted.db.execute("SELECT COUNT(*) FROM event_occurrences").fetchone()[0], 1)
            restarted.close()

    def test_architecture_guards_keep_core_surfaces_provider_agnostic(self):
        root = Path(__file__).parents[1] / "enotify"
        worker_source = (root / "worker.py").read_text(encoding="utf-8").lower()
        entry_source = (root.parent / "enotify-worker.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("providers.events.typing", worker_source)
        self.assertNotIn("providers.events.typing", entry_source)
        self.assertNotIn("typing-transitions", worker_source)
        self.assertNotIn("typing-transitions", entry_source)
        tree = ast.parse((root / "storage.py").read_text(encoding="utf-8"))
        store = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Store")
        names = {node.name for node in store.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertFalse(any(name.startswith("typing") or name.startswith("buzz") for name in names))
