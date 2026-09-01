import unittest

from enotify.models import EventTriggerSpec, canonical_json


class ModelTests(unittest.TestCase):
    def test_json_only_envelope_and_determinism(self):
        spec = EventTriggerSpec.from_mapping(
            {
                "provider": "github",
                "event_type": "check",
                "schema_version": 1,
                "match": {"b": 1, "a": 2},
            }
        )
        self.assertEqual(canonical_json(spec.envelope()["match"]), '{"a":2,"b":1}')
        with self.assertRaises(ValueError):
            EventTriggerSpec.from_mapping(
                {
                    "provider": "github",
                    "event_type": "check",
                    "schema_version": 1,
                    "match": {},
                    "selector": "github/check",
                }
            )
