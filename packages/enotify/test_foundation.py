import tempfile, unittest
from pathlib import Path
from enotify.models import EventTriggerSpec, NotificationAddressSpec, canonical_json
from enotify.providers.events import default_registry
from enotify.providers.notifications import default_registry as notifications
from enotify.storage import Store, Conflict

class FoundationTests(unittest.TestCase):
    def specs(self):
        return EventTriggerSpec("github","check",1,{"repository":"x"}), NotificationAddressSpec("buzz","message",1,{"community":"c","channel":"ch"})
    def test_json_specs_and_determinism(self):
        e,n=self.specs(); self.assertEqual(e.envelope()["event_type"],"check"); self.assertEqual(canonical_json({"b":1,"a":2}), '{"a":2,"b":1}')
        with self.assertRaises(ValueError): EventTriggerSpec("event:github/pr-check","check",1,{})
    def test_role_qualified_registries_are_separate(self):
        self.assertEqual(default_registry().get("buzz","channel").role,"event")
        self.assertEqual(notifications().get("buzz","message").role,"notification")
        with self.assertRaises(KeyError): default_registry().get("github","missing")
        with self.assertRaises(KeyError): notifications().get("buzz","missing")
    def test_crud_revision_reservation_and_acceptance(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/"db.sqlite"); s.open(); e,n=self.specs(); item=s.create("one",e,n)
            self.assertTrue(s.reserve_one(item["id"],"occ-1")); self.assertFalse(s.reserve_one(item["id"],"occ-2"))
            with self.assertRaises(Conflict): s.transition(item["id"],"pause",99)
            s.accepted(item["id"],"occ-1","delivery-1"); self.assertEqual(s.get(item["id"])["state"],"finished"); s.close()
    def test_failed_attempt_does_not_consume_one_and_all_continues(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/"db.sqlite"); s.open(); e,n=self.specs()
            one=s.create("one",e,n); self.assertTrue(s.reserve_one(one["id"],"failed")); s.record_attempt(one["id"],"failed",1,"retryable","timeout")
            self.assertEqual(s.get(one["id"])["state"],"active"); self.assertFalse(s.reserve_one(one["id"],"later"))
            all_sub=s.create("all",e,n); s.accepted(all_sub["id"],"a","d-a"); self.assertEqual(s.get(all_sub["id"])["state"],"active"); s.close()
