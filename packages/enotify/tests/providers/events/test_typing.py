import json
import os
import threading
import time
import unittest
from io import StringIO
from unittest.mock import patch

from enotify.providers.events.typing import (
    BuzzTypingLiveStream,
    BuzzTypingTransitionsProvider,
    _TypingStreamPool,
)


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
        stream._config = {"executable": "/opt/buzz-server/current/buzz-events"}
        command = stream.command
        self.assertEqual(command[:3], ["/opt/buzz-server/current/buzz-events", "subscribe", "--community"])
        self.assertNotIn("messages", command)
        self.assertEqual(json.loads(command[-1]), {"kinds": [20002], "authors": ["author"], "#h": ["channel"]})

    def test_live_stream_command_is_configurable_and_inherits_environment(self):
        stream = BuzzTypingLiveStream.__new__(BuzzTypingLiveStream)
        stream.community, stream.channel, stream.author = "community", "channel", "author"
        stream._config = {"executable": "/srv/buzz-events"}
        with patch.dict(os.environ, {"BUZZ_RELAY_URL": "relay", "BUZZ_AUTH_TAG": "tag"}, clear=True):
            self.assertEqual(stream.command[0], "/srv/buzz-events")
            self.assertEqual(stream._child_env()["BUZZ_AUTH_TAG"], "tag")

    def test_runner_stream_uses_buzz_events_and_inherited_environment(self):
        calls = []
        class Result:
            stdout = ""
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return Result()
        stream = __import__("enotify.providers.events.typing", fromlist=["_RunnerTypingLiveStream"])._RunnerTypingLiveStream(
            run, {"community": "community", "channel": "channel", "author": "author", "executable": "/srv/buzz-events"}
        )
        with patch.dict(os.environ, {"BUZZ_AUTH_TAG": "tag"}, clear=True):
            stream.poll()
        self.assertEqual(calls[0][0][0], "/srv/buzz-events")
        self.assertEqual(calls[0][1]["env"]["BUZZ_AUTH_TAG"], "tag")

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
        for _ in range(100):
            if children:
                break
            time.sleep(0.01)
        self.assertEqual(len(children), 1)
        os.write(write_fd, b'{"type":"notice","message":"ignored"}\nnot json\n')
        event_line = (json.dumps({"type": "event", "event": tick("live", 100)}) + "\n").encode()
        os.write(write_fd, event_line[:12])
        os.write(write_fd, event_line[12:])
        self.assertEqual(stream.poll(), [])
        os.write(write_fd, b'{"type":"eose"}\n')
        rows = []
        for _ in range(100):
            rows = stream.poll()
            if rows and stream._ready:
                break
            time.sleep(0.01)
        self.assertEqual([row["id"] for row in rows], ["live"])
        os.close(write_fd)
        stream.close()
        self.assertTrue(children[0][1].terminated)

    def test_eof_reconnects_after_backoff_and_requires_new_eose(self):
        children, writers = [], []

        class Child:
            def __init__(self, reader):
                self.stdout, self.terminated = reader, False
            def terminate(self):
                self.terminated = True
            def wait(self, timeout=None):
                return 0
            def kill(self):
                self.terminated = True

        def popen(command, **kwargs):
            read_fd, write_fd = os.pipe()
            reader = os.fdopen(read_fd, "r")
            writers.append(write_fd)
            child = Child(reader)
            children.append(child)
            return child

        stream = BuzzTypingLiveStream("c", "ch", "a", popen_factory=popen)
        for _ in range(20):
            if writers:
                break
            time.sleep(0.01)
        self.assertEqual(len(writers), 1)
        os.write(writers[0], (json.dumps({"type": "eose"}) + "\n").encode())
        for _ in range(20):
            if stream.poll() == [] and stream._ready:
                break
            time.sleep(0.01)
        os.close(writers[0])
        for _ in range(160):
            if len(children) >= 2:
                break
            time.sleep(0.01)
        self.assertEqual(len(children), 2)
        self.assertFalse(stream._ready)
        os.write(writers[1], (json.dumps({"type": "eose"}) + "\n").encode())
        for _ in range(20):
            if stream._ready:
                break
            time.sleep(0.01)
        self.assertTrue(stream._ready)
        os.close(writers[1])
        stream.close()
        self.assertTrue(all(child.terminated for child in children))

    def test_pool_prunes_orphans_closes_all_and_wakes(self):
        class Managed:
            def __init__(self, signaled=False):
                self.closed = False
                self.signaled = signaled
            def close(self):
                self.closed = True
            def wait(self, timeout):
                return self.signaled

        pool = _TypingStreamPool()
        retained = Managed()
        orphan = Managed()
        pool._streams = {("c", "ch", "a"): retained, ("c", "old", "a"): orphan}
        pool.prune({("c", "ch", "a")})
        self.assertFalse(retained.closed)
        self.assertTrue(orphan.closed)
        wake = Managed(signaled=True)
        pool._streams[("c", "other", "a")] = wake
        self.assertTrue(pool.wait(0.2))
        pool.close_all()
        self.assertTrue(retained.closed)
        self.assertTrue(wake.closed)
        self.assertEqual(pool._streams, {})

    def test_same_observation_group_shares_one_child_for_multiple_ttls(self):
        created = []
        class Shared:
            def close(self):
                pass
            def wait(self, timeout):
                return False
            def health(self):
                return {"ready": True, "error": None, "backoff_until": 0}
        def factory(community, channel, author, wake=None):
            stream = Shared()
            created.append((community, channel, author, stream))
            return stream
        pool = _TypingStreamPool(factory)
        first = pool.stream("c", "ch", "a")
        second = pool.stream("c", "ch", "a")
        other = pool.stream("c", "ch", "b")
        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertEqual(len(created), 2)
        pool.close_all()

    def test_supervisor_caps_pre_eose_backoff_and_resets_after_real_eose(self):
        class Clock:
            now = 0.0
            def __call__(self):
                return self.now

        class Gate:
            def __init__(self, clock):
                self.clock, self.delays, self.stopped = clock, [], False
            def is_set(self):
                return self.stopped
            def wait(self, delay):
                self.delays.append(delay)
                self.clock.now += delay
                return False
            def set(self):
                self.stopped = True

        class Output:
            def __iter__(self):
                yield '{"type":"eose"}'
                while not gate.is_set():
                    time.sleep(0.001)

        class Child:
            def __init__(self):
                self.stdout = Output()
                self.returncode = 0
            def terminate(self):
                gate.set()
            def wait(self, timeout=None):
                return 0
            def kill(self):
                gate.set()

        clock = Clock()
        gate = Gate(clock)
        attempts, observed = [], []
        eose_seen = threading.Event()

        class ObservedStream(BuzzTypingLiveStream):
            def _consume(self, line):
                super()._consume(line)
                if json.loads(line).get("type") == "eose":
                    observed.append(self._backoff)
                    eose_seen.set()

        def popen(command, **kwargs):
            attempts.append(len(attempts) + 1)
            if len(attempts) <= 6:
                raise OSError("unavailable")
            return Child()

        stream = ObservedStream("c", "ch", "a", popen_factory=popen, clock=clock, stop_event=gate)
        self.assertTrue(eose_seen.wait(1))
        self.assertEqual(len(attempts), 7)
        self.assertGreaterEqual(max(gate.delays), 16.0)
        self.assertEqual(stream._backoff, 1.0)
        self.assertEqual(observed, [1.0])
        stream.close()

    def test_real_stream_close_terminates_blocking_reader_and_backoff_wait(self):
        children = []
        class Child:
            def __init__(self):
                self.read_fd, self.write_fd = os.pipe()
                self.stdout = os.fdopen(self.read_fd, "r")
                self.returncode = None
                self.terminated = False
            def terminate(self):
                self.terminated = True
                os.close(self.write_fd)
            def wait(self, timeout=None):
                self.returncode = 0
            def kill(self):
                self.terminated = True

        def popen(command, **kwargs):
            child = Child()
            children.append(child)
            return child

        blocking = BuzzTypingLiveStream("c", "blocking", "a", popen_factory=popen)
        for _ in range(100):
            if children:
                break
            time.sleep(0.005)
        self.assertTrue(children)
        blocking.close()
        self.assertTrue(children[0].terminated)
        self.assertTrue(children[0].stdout.closed)
        self.assertFalse(blocking._thread.is_alive())

        def unavailable(*args, **kwargs):
            raise OSError("down")
        failing = BuzzTypingLiveStream("c", "backoff", "a", popen_factory=unavailable)
        time.sleep(0.01)
        failing.close()
        self.assertFalse(failing._thread.is_alive())

    def test_health_reporting_is_transition_based_and_recovery_clears_error(self):
        source = ("c", "ch", "a")
        reported = {}
        stderr = StringIO()
        with patch("sys.stderr", stderr):
            from importlib.util import module_from_spec, spec_from_file_location
            spec = spec_from_file_location("enotify_worker_test", os.path.join(os.path.dirname(__file__), "..", "..", "..", "enotify-worker.py"))
            module = module_from_spec(spec)
            spec.loader.exec_module(module)
            module.report_typing_health({"source": source, "error": "spawn-failure"}, reported)
            module.report_typing_health({"source": source, "error": "spawn-failure"}, reported)
            module.report_typing_health({"source": source, "error": None}, reported)
            module.report_typing_health({"source": source, "error": None}, reported)
        self.assertEqual(stderr.getvalue().count("enotify typing stream status:"), 2)
        self.assertIn("spawn-failure", stderr.getvalue())
        self.assertIn("recovered", stderr.getvalue())

    def test_signal_wake_is_immediate_and_service_finally_closes_streams(self):
        pool = _TypingStreamPool()
        waiter = threading.Thread(target=lambda: pool.wait(10), daemon=True)
        waiter.start()
        started = time.monotonic()
        pool._wake.set()
        waiter.join(1)
        self.assertFalse(waiter.is_alive())
        self.assertLess(time.monotonic() - started, 1)

        from importlib.util import module_from_spec, spec_from_file_location
        worker_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "enotify-worker.py")
        spec = spec_from_file_location("enotify_worker_shutdown_test", worker_path)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        class FakeStore:
            def __init__(self, path): pass
            def open(self): pass
            def close(self): self.closed = True
        module.stopping = True
        with patch.object(module, "Store", FakeStore), patch.object(module.signal, "signal"), patch.object(module, "close_typing_streams") as close:
            self.assertEqual(module.main(), 0)
        close.assert_called_once()
        module.stopping = False


if __name__ == "__main__":
    unittest.main()
