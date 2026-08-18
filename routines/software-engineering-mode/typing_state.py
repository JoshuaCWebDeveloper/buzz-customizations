#!/usr/bin/env python3
"""Emit transition-only state for one author's channel typing events."""

import argparse
import json
import selectors
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


DEFAULT_TYPING_TTL = 6


def output_record(state: str, timestamp: int) -> str:
    return json.dumps({"timestamp": timestamp, "state": state}, separators=(",", ":"))


def event_tick(value: Any, channel: str, author: str) -> Optional[int]:
    """Return a valid typing tick, or None for unrelated/control/malformed input."""
    if not isinstance(value, dict) or value.get("type") != "event":
        return None
    event = value.get("event")
    if not isinstance(event, dict):
        return None
    if event.get("kind") != 20002 or event.get("pubkey") != author:
        return None
    created_at = event.get("created_at")
    if not isinstance(created_at, int) or isinstance(created_at, bool) or created_at < 0:
        return None
    tags = event.get("tags")
    if not isinstance(tags, list):
        return None
    if not any(isinstance(tag, list) and len(tag) >= 2 and tag[0] == "h" and tag[1] == channel for tag in tags):
        return None
    return created_at


@dataclass
class TypingState:
    ttl: int = DEFAULT_TYPING_TTL
    active: bool = False
    expires_at: Optional[int] = None

    def initial(self, now: int) -> str:
        self.active = False
        self.expires_at = None
        return "typing_stopped"

    def tick(self, timestamp: int, observed_at: Optional[int] = None) -> Optional[tuple[str, int]]:
        """Apply a fresh event tick and return a transition, if any."""
        if timestamp < 0 or (observed_at is not None and timestamp + self.ttl <= observed_at):
            return None
        was_active = self.active and self.expires_at is not None and timestamp < self.expires_at
        self.expires_at = timestamp + self.ttl
        self.active = True
        if not was_active:
            return "typing_started", timestamp
        return None

    def advance(self, now: int) -> Optional[tuple[str, int]]:
        """Expire at the semantic deadline, never at the later observation time."""
        if self.active and self.expires_at is not None and now >= self.expires_at:
            expiry = self.expires_at
            self.active = False
            self.expires_at = None
            return "typing_stopped", expiry
        return None


def filter_json(channel: str, author: str) -> str:
    return json.dumps({"kinds": [20002], "authors": [author], "#h": [channel]}, separators=(",", ":"))


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--community", required=True, help="Buzz community id")
    parser.add_argument("--user", required=True, help="Managed subscriber user id")
    parser.add_argument("--channel", required=True, help="Target channel UUID")
    parser.add_argument("--author", required=True, help="Target author pubkey")
    parser.add_argument("--ttl", type=int, default=DEFAULT_TYPING_TTL, help="Typing TTL in seconds (default: 6)")
    return parser.parse_args(argv)


def run(args: argparse.Namespace, clock: Callable[[], int] = lambda: int(time.time())) -> int:
    if args.ttl <= 0:
        print("typing-state: --ttl must be positive", file=sys.stderr)
        return 64
    command = [
        "buzz-server", "events", "subscribe", "--community", args.community,
        "--user", args.user, "--filter", filter_json(args.channel, args.author),
    ]
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, text=True, bufsize=1)
    except OSError as error:
        print(f"typing-state: unable to start buzz-server: {error}", file=sys.stderr)
        return 127

    state = TypingState(args.ttl)
    startup_now = clock()
    print(output_record(state.initial(startup_now), startup_now), flush=True)
    selector = selectors.DefaultSelector()
    assert child.stdout is not None
    selector.register(child.stdout, selectors.EVENT_READ)
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
        child.terminate()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopping:
            now = clock()
            transition = state.advance(now)
            if transition:
                print(output_record(*transition), flush=True)
            timeout = None
            if state.expires_at is not None:
                timeout = max(0, state.expires_at - now)
            ready = selector.select(timeout)
            if not ready:
                continue
            line = child.stdout.readline()
            if not line:
                break
            try:
                tick = event_tick(json.loads(line), args.channel, args.author)
            except json.JSONDecodeError:
                tick = None
            if tick is not None:
                transition = state.tick(tick, clock())
                if transition:
                    print(output_record(*transition), flush=True)
    finally:
        selector.close()
        if child.poll() is None:
            child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    return child.returncode if child.returncode not in (0, -signal.SIGTERM, -signal.SIGINT) else 0


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
