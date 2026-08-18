#!/usr/bin/env python3
import importlib.util
import json
import unittest
from pathlib import Path


MODULE = Path(__file__).with_name("typing_state.py")
spec = importlib.util.spec_from_file_location("typing_state", MODULE)
typing_state = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(typing_state)


def event(at):
    return {"type": "event", "event": {
        "kind": 20002, "pubkey": "author", "created_at": at,
        "tags": [["h", "channel"]],
    }}


class TypingStateTests(unittest.TestCase):
    def test_initial_stopped(self):
        state = typing_state.TypingState()
        self.assertEqual(state.initial(100), "typing_stopped")

    def test_initial_started_from_fresh_observation(self):
        state = typing_state.TypingState()
        self.assertEqual(state.tick(100), ("typing_started", 100))
        self.assertIsNone(state.advance(105))

    def test_refresh_ticks_collapse(self):
        state = typing_state.TypingState()
        self.assertEqual(state.tick(100), ("typing_started", 100))
        self.assertIsNone(state.tick(103))
        self.assertEqual(state.expires_at, 109)

    def test_stops_at_semantic_expiry(self):
        state = typing_state.TypingState()
        state.tick(100)
        self.assertEqual(state.advance(105), None)
        self.assertEqual(state.advance(108), ("typing_stopped", 106))

    def test_restart_after_expiry(self):
        state = typing_state.TypingState()
        state.tick(100)
        self.assertEqual(state.advance(106), ("typing_stopped", 106))
        self.assertEqual(state.tick(109), ("typing_started", 109))

    def test_expired_delayed_tick_does_not_restart(self):
        state = typing_state.TypingState()
        self.assertIsNone(state.tick(100, observed_at=106))
        self.assertFalse(state.active)

    def test_event_filtering_and_compact_output(self):
        self.assertEqual(typing_state.event_tick(event(100), "channel", "author"), 100)
        self.assertIsNone(typing_state.event_tick(event(100), "other", "author"))
        record = typing_state.output_record("typing_stopped", 106)
        self.assertEqual(record, '{"timestamp":106,"state":"typing_stopped"}')
        self.assertEqual(json.loads(record)["state"], "typing_stopped")


if __name__ == "__main__":
    unittest.main()
