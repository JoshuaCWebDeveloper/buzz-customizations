from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path

from enotify.providers.events import EventOccurrence
from enotify.storage import Conflict, Store
from tests.helpers import specs


class StorageTests(unittest.TestCase):
    def open_store(self, directory):
        store = Store(Path(directory) / "enotify.sqlite")
        store.open()
        return store

    def occurrence(self, occurrence_id="occurrence"):
        return EventOccurrence("fake", "source", occurrence_id, "2026-09-01T00:00:00Z")

    def test_migrations_are_repeatable_and_wal_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            first = self.open_store(directory)
            self.assertEqual(first.status()["migration_version"], 5)
            self.assertEqual(first.status()["journal_mode"], "wal")
            first.close()
            second = self.open_store(directory)
            self.assertEqual(
                second.db.execute("SELECT COUNT(*) FROM migrations").fetchone()[0], 5
            )
            second.close()

    def test_crud_revision_state_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open_store(directory)
            event, notification = specs()
            item = store.create("one", event, notification)
            updated = store.update(item["id"], item["revision"], "all")
            with self.assertRaises(Conflict):
                store.update(item["id"], item["revision"], "one")
            paused = store.transition(updated["id"], "pause", updated["revision"])
            resumed = store.transition(paused["id"], "resume", paused["revision"])
            self.assertEqual(resumed["state"], "active")
            first = store.mutate_idempotent(
                "key", "test", {"value": 1}, lambda: {"token": "secret", "ok": True}
            )
            replay = store.mutate_idempotent(
                "key", "test", {"value": 1}, lambda: {"token": "changed"}
            )
            self.assertEqual(first, replay)
            self.assertEqual(replay["token"], "[redacted]")
            with self.assertRaises(Conflict):
                store.mutate_idempotent("key", "test", {"value": 2}, lambda: {})
            store.close()

    def test_single_winner_one_reservation_under_concurrency(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = self.open_store(directory)
            event, notification = specs()
            subscription = seed.create("one", event, notification)
            first = seed.record_occurrence(self.occurrence("one"))
            second = seed.record_occurrence(self.occurrence("two"))
            seed.close()

            def reserve(row_id):
                store = self.open_store(directory)
                try:
                    return store.reserve(subscription["id"], row_id)
                finally:
                    store.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(reserve, (first["id"], second["id"])))
            self.assertEqual(sum(result is not None for result in results), 1)

    def test_typing_projection_serializes_equal_ticks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "enotify.sqlite"
            seed = self.open_store(directory)
            seed.close()
            def make(direction, timestamp, observed):
                prior, new = (("typing", "not-typing") if direction == "stopped" else ("not-typing", "typing"))
                return EventOccurrence("buzz", "typing-source", f"{direction}-{timestamp}", str(observed), str(timestamp), {"direction": direction, "prior_state": prior, "new_state": new})
            def apply(_):
                store = Store(path)
                store.open()
                try:
                    return store.process_typing_tick("buzz", "typing-source", "tick", 100, 100, 8, make, lambda _: True)
                finally:
                    store.close()
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(apply, (1, 2)))
            self.assertEqual(sum(len(result) for result in results), 1)
            check = self.open_store(directory)
            self.assertEqual(check.typing_projection("buzz", "typing-source")["expires_at"], 108)
            check.close()

    def test_late_acceptance_is_recorded_without_resurrection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open_store(directory)
            event, notification = specs()
            subscription = store.create("one", event, notification)
            occurrence = store.record_occurrence(self.occurrence())
            reservation = store.reserve(subscription["id"], occurrence["id"])
            claim = store.claim(reservation["id"], "worker")
            paused = store.transition(
                subscription["id"], "pause", subscription["revision"]
            )
            self.assertEqual(
                store.accepted(reservation["id"], claim["attempt"], "receipt"),
                "accepted_late",
            )
            self.assertEqual(store.get(subscription["id"])["state"], "paused")
            self.assertEqual(paused["revision"], store.get(subscription["id"])["revision"])
            store.close()

    def test_expired_lease_is_reclaimed_for_same_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open_store(directory)
            event, notification = specs()
            subscription = store.create("one", event, notification)
            occurrence = store.record_occurrence(self.occurrence())
            reservation = store.reserve(subscription["id"], occurrence["id"])
            first = store.claim(reservation["id"], "worker-one", ttl_seconds=-1)
            self.assertIsNotNone(first)
            self.assertEqual(store.reclaim_expired(), 1)
            second = store.claim(reservation["id"], "worker-two")
            self.assertEqual(second["attempt"], 2)
            self.assertTrue(store.heartbeat(reservation["id"], "worker-two"))
            self.assertFalse(store.heartbeat(reservation["id"], "worker-one"))
            store.close()
