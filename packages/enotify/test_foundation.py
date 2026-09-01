import tempfile, unittest
from pathlib import Path
from enotify.models import EventTriggerSpec, NotificationAddressSpec, canonical_json
from enotify.providers.events import default_registry
from enotify.providers.notifications import default_registry as notifications
from enotify.storage import Store, Conflict
from enotify.worker import Worker
from enotify.providers.events import EventOccurrence
from enotify.providers.notifications import AcceptedDelivery

class FoundationTests(unittest.TestCase):
    def specs(self):
        return EventTriggerSpec("github","check",1,{"repository":"x"}), NotificationAddressSpec("buzz","message",1,{"community":"c","channel":"ch"})
    def test_json_specs_and_determinism(self):
        e,n=self.specs(); self.assertEqual(e.envelope()["event_type"],"check"); self.assertEqual(canonical_json({"b":1,"a":2}), '{"a":2,"b":1}')
        with self.assertRaises(ValueError): EventTriggerSpec("event:github/pr-check","check",1,{})
    def test_role_qualified_registries_are_separate(self):
        self.assertEqual(default_registry().get("buzz","channel-events").role,"event")
        self.assertEqual(notifications().get("buzz","message").role,"notification")
        with self.assertRaises(KeyError): default_registry().get("github","missing")
        with self.assertRaises(KeyError): notifications().get("buzz","missing")
    def test_provider_schema_fixtures_reject_unknown_fields(self):
        registry=default_registry()
        self.assertEqual(registry.get("buzz","channel-events").validate_config({"community":"com","channel":"c","author":"a","kind":20002},1)["channel"],"c")
        self.assertEqual(registry.get("github","check").validate_config({"repository":"r","check":{"name":"ci"},"pull_request":{"number":42}},1)["pull_request"]["number"],42)
        self.assertEqual(registry.get("system-process","exited").validate_config({"pid":7,"start_identity":"s"},1)["pid"],7)
        for provider, event_type in (("buzz","channel-events"),("github","check"),("system-process","exited")):
            with self.assertRaises(ValueError): registry.get(provider,event_type).validate_config({"unexpected":True},1)
    def test_buzz_notification_address_fixture(self):
        provider=notifications().get("buzz","message")
        self.assertEqual(provider.validate_config({"community":"community","channel":"channel"},1)["channel"],"channel")
        self.assertEqual(provider.validate_config({"community":"community","channel":"channel","mention":"Alice"},1)["mention"],"Alice")
        with self.assertRaises(ValueError): provider.validate_config({"community":"c"},1)
        with self.assertRaises(ValueError): provider.validate_config({"community":"c","channel":"ch","secret":"x"},1)
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
    def test_exhaustion_fails_closed_and_redacts_idempotent_result(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/"db.sqlite"); s.open(); e,n=self.specs(); item=s.create("one",e,n)
            self.assertTrue(s.reserve_one(item["id"],"occ"))
            result=s.exhaust_one(item["id"],"occ"); self.assertEqual(result["state"],"paused")
            self.assertTrue(s.release_one(item["id"],"occ")); self.assertFalse(s.release_one(item["id"],"occ"))
            value=s.mutate_idempotent("k","update",lambda: {"token":"secret","ok":True})
            self.assertEqual(value["token"],"[redacted]")
            self.assertEqual(s.mutate_idempotent("k","update",lambda: {"token":"changed"})["token"],"[redacted]")
            s.close()
    def test_fake_worker_one_and_all_flows(self):
        class Events:
            def observe(self, cursor): return [EventOccurrence("o1","fake","now"),EventOccurrence("o2","fake","now")]
        class Notifications:
            def __init__(self): self.sent=[]
            def send(self, message, key): self.sent.append(key); return AcceptedDelivery("d-"+key[-2:])
        with tempfile.TemporaryDirectory() as d:
            s=Store(Path(d)/"db.sqlite"); s.open(); e,n=self.specs(); p=Notifications()
            one=s.create("one",e,n); Worker(s,Events(),p).process(one,lambda o:"message")
            self.assertEqual(len(p.sent),1); self.assertEqual(s.get(one["id"])["state"],"finished")
            all_sub=s.create("all",e,n); Worker(s,Events(),p).process(all_sub,lambda o:"message")
            self.assertEqual(len(p.sent),3); self.assertEqual(s.get(all_sub["id"])["state"],"active"); s.close()
