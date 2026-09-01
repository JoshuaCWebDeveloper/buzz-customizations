#!/usr/bin/env python3
"""JSON-only enotify provider and subscription control CLI."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from enotify.models import EventTriggerSpec, NotificationAddressSpec
from enotify.providers.events import default_registry as event_registry
from enotify.providers.notifications import default_registry as notification_registry
from enotify.storage import Conflict, Store


def read_json(source: str) -> dict[str, Any]:
    stripped = source.lstrip()
    text = source if stripped.startswith("{") else (
        sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    )
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("spec must be a JSON object")
    return value


def event_spec(source: str) -> EventTriggerSpec:
    raw = EventTriggerSpec.from_mapping(read_json(source))
    provider = event_registry().get(raw.provider, raw.event_type)
    normalized = provider.validate_config(dict(raw.match), raw.schema_version)
    return EventTriggerSpec(raw.provider, raw.event_type, raw.schema_version, normalized)


def notification_spec(source: str) -> NotificationAddressSpec:
    raw = NotificationAddressSpec.from_mapping(read_json(source))
    provider = notification_registry().get(raw.provider, raw.notification_type)
    normalized = provider.validate_config(dict(raw.address), raw.schema_version)
    return NotificationAddressSpec(
        raw.provider, raw.notification_type, raw.schema_version, normalized
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="enotify")
    root.add_argument(
        "--db",
        default=os.environ.get(
            "ENOTIFY_DB", str(Path.home() / ".local/state/enotify/enotify.db")
        ),
    )
    commands = root.add_subparsers(dest="command", required=True)

    providers = commands.add_parser("provider")
    provider_commands = providers.add_subparsers(dest="provider_action", required=True)
    provider_commands.add_parser("list")
    describe = provider_commands.add_parser("describe")
    describe.add_argument("role", choices=("event", "notification"))
    describe.add_argument("provider")
    describe.add_argument("capability")

    subscriptions = commands.add_parser("subscription")
    subscription_commands = subscriptions.add_subparsers(dest="action", required=True)
    create = subscription_commands.add_parser("create")
    create.add_argument("--frequency", choices=("one", "all"), required=True)
    create.add_argument("--event-spec", required=True, metavar="JSON|FILE|-")
    create.add_argument("--notification-spec", required=True, metavar="JSON|FILE|-")

    listing = subscription_commands.add_parser("list")
    listing.add_argument("--state")
    for action in ("get", "status"):
        command = subscription_commands.add_parser(action)
        command.add_argument("id")
    for action in ("pause", "resume", "delete"):
        command = subscription_commands.add_parser(action)
        command.add_argument("id")
        command.add_argument("--if-revision", type=int, required=True)

    update = subscription_commands.add_parser("update")
    update.add_argument("id")
    update.add_argument("--if-revision", type=int, required=True)
    update.add_argument("--frequency", choices=("one", "all"))
    update.add_argument("--event-spec", metavar="JSON|FILE|-")
    update.add_argument("--notification-spec", metavar="JSON|FILE|-")

    deliveries = subscription_commands.add_parser("deliveries")
    deliveries.add_argument("id")
    deliveries.add_argument("--limit", type=int, default=100)
    retry = subscription_commands.add_parser("retry")
    retry.add_argument("id")
    retry.add_argument("--reservation", required=True)
    retry.add_argument("--if-revision", type=int, required=True)
    release = subscription_commands.add_parser("release")
    release.add_argument("id")
    release.add_argument("--reservation", required=True)
    release.add_argument("--if-revision", type=int, required=True)
    release.add_argument("--resume", action="store_true")

    commands.add_parser("status")
    commands.add_parser("doctor")
    return root


def provider_command(args: argparse.Namespace) -> Any:
    events = event_registry()
    notifications = notification_registry()
    if args.provider_action == "list":
        return {"events": events.describe(), "notifications": notifications.describe()}
    registry = events if args.role == "event" else notifications
    return registry.get(args.provider, args.capability).describe()


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == "provider":
            result = provider_command(args)
        else:
            store = Store(Path(args.db))
            store.open()
            try:
                if args.command in ("status", "doctor"):
                    result = store.status()
                elif args.action == "create":
                    if args.event_spec == "-" and args.notification_spec == "-":
                        raise ValueError("only one spec may read from stdin")
                    result = store.create(
                        args.frequency,
                        event_spec(args.event_spec),
                        notification_spec(args.notification_spec),
                    )
                elif args.action == "list":
                    result = store.list(args.state)
                elif args.action in ("get", "status"):
                    result = store.get(args.id)
                elif args.action in ("pause", "resume", "delete"):
                    result = store.transition(args.id, args.action, args.if_revision)
                elif args.action == "update":
                    if args.event_spec == "-" and args.notification_spec == "-":
                        raise ValueError("only one spec may read from stdin")
                    result = store.update(
                        args.id,
                        args.if_revision,
                        args.frequency,
                        event_spec(args.event_spec) if args.event_spec else None,
                        notification_spec(args.notification_spec)
                        if args.notification_spec
                        else None,
                    )
                elif args.action == "deliveries":
                    result = store.deliveries(args.id, args.limit)
                elif args.action == "retry":
                    result = store.request_retry(
                        args.id, args.reservation, args.if_revision
                    )
                elif args.action == "release":
                    result = store.release(
                        args.id,
                        args.reservation,
                        args.if_revision,
                        args.resume,
                    )
                else:
                    raise ValueError("unsupported action")
            finally:
                store.close()
        print(json.dumps(result, sort_keys=True))
        return 0
    except Conflict as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
