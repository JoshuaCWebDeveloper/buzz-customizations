from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from enotify.providers.events import EventOccurrence
from enotify.models import NotificationAddressSpec
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
            self.assertEqual(first.status()["migration_version"], 6)
            self.assertEqual(first.status()["journal_mode"], "wal")
            first.close()
            second = self.open_store(directory)
            self.assertEqual(
                second.db.execute("SELECT COUNT(*) FROM migrations").fetchone()[0], 6
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

    def test_custom_content_survives_store_reload_without_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open_store(directory)
            event, _ = specs()
            notification = NotificationAddressSpec(
                "buzz", "message", 1,
                {"community": "community", "channel": "channel", "content": "{author} has {direction} working"},
            )
            subscription = store.create("all", event, notification)
            store.close()
            reloaded = self.open_store(directory)
            self.assertEqual(
                reloaded.get(subscription["id"])["notification_address"]["address"]["content"],
                "{author} has {direction} working",
            )
            self.assertEqual(reloaded.status()["migration_version"], 6)
            reloaded.close()

    def test_matching_active_all_is_rejected_but_one_repeats(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open_store(directory)
            event, notification = specs()
            first = store.create("all", event, notification)
            with self.assertRaisesRegex(Conflict, first["id"]):
                store.create("all", event, notification)
            second = store.create("one", event, notification)
            third = store.create("one", event, notification)
            self.assertNotEqual(second["id"], third["id"])
            store.close()

    def test_paused_all_blocks_and_deleted_all_can_be_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = self.open_store(directory)
            event, notification = specs()
            paused = store.create("all", event, notification)
            paused = store.transition(paused["id"], "pause", paused["revision"])
            with self.assertRaisesRegex(Conflict, paused["id"]):
                store.create("all", event, notification)
            deleted = store.transition(paused["id"], "delete", paused["revision"])
            self.assertEqual(deleted["state"], "deleted")
            recreated = store.create("all", event, notification)
            self.assertNotEqual(recreated["id"], paused["id"])
            store.close()

    def test_migration_retires_duplicate_all_rows_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            old_migrations = Path(directory) / "old-migrations"
            old_migrations.mkdir()
            package_migrations = Path(__file__).parents[1] / "migrations"
            for path in sorted(package_migrations.glob("00[1-5]_*.sql")):
                shutil.copy2(path, old_migrations / path.name)
            database = Path(directory) / "legacy.sqlite"
            legacy = Store(database, old_migrations)
            legacy.open()
            event, notification = specs()
            event_json = json.dumps(event.envelope(), sort_keys=True, separators=(",", ":"))
            notification_json = json.dumps(notification.envelope(), sort_keys=True, separators=(",", ":"))
            legacy.db.executemany(
                "INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    ("older", 1, "all", event_json, notification_json, "active", None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
                    ("newer", 1, "all", event_json, notification_json, "paused", None, "2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z"),
                ],
            )
            legacy.close()
            store = Store(database)
            store.open()
            rows = store.db.execute("SELECT id,state,reason FROM subscriptions ORDER BY id").fetchall()
            self.assertEqual(
                [(row["id"], row["state"], row["reason"]) for row in rows],
                [("newer", "deleted", "duplicate_frequency_all"), ("older", "active", None)],
            )
            with self.assertRaisesRegex(Conflict, "older"):
                store.create("all", event, notification)
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 2)
            store.close()

    def test_concurrent_matching_all_creates_have_one_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            seed = self.open_store(directory)
            event, notification = specs()
            seed.close()

            def create(_):
                store = self.open_store(directory)
                try:
                    return store.create("all", event, notification)
                except Conflict as exc:
                    return str(exc)
                finally:
                    store.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(create, (1, 2)))
            self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
            winner = next(result for result in results if isinstance(result, dict))
            loser = next(result for result in results if isinstance(result, str))
            self.assertIn(winner["id"], loser)

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
