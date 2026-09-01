# enotify

`enotify` is an independent, package-owned notification control-plane foundation. It owns its SQLite WAL database, numbered forward migrations, provider registries, lifecycle CLI, delivery ledger, tests, and explicit deploy/undeploy scaffolding. It does not import Buzz Server internals or change Buzz Server state.

## Persisted contract

Event and notification specs are JSON-only and deliberately distinct:

```json
{"provider":"github","event_type":"check","schema_version":1,"match":{"repository":"owner/repo","check":{"name":{"equals":"ci"},"status":{"in":["completed"]}}}}
```

```json
{"provider":"buzz","notification_type":"message","schema_version":1,"address":{"community":"community-id","channel":"channel-id","mention":{"pubkey":"hex-or-npub","handle":"Alice"}}}
```

Providers reject unknown fields and normalize accepted values before persistence. Event and notification interfaces, registries, implementations, and tests are physically separate; `buzz/channel-events` and `buzz/message` resolve only through their role-specific registries.

The initial provider implementations are validation and extension seams. They do not yet observe Buzz, GitHub, or process events and do not publish Buzz messages. `system-process/exited` describes an already-running PID and never launches or supervises a process.

## CLI

Run from the repository root with `PYTHONPATH=packages/enotify`:

```bash
python3 packages/enotify/enotify.py provider list
python3 packages/enotify/enotify.py provider describe event github check
python3 packages/enotify/enotify.py subscription create \
  --frequency one \
  --event-spec event.json \
  --notification-spec notification.json
python3 packages/enotify/enotify.py subscription update SUBSCRIPTION_ID \
  --if-revision 1 \
  --event-spec event.json
python3 packages/enotify/enotify.py subscription pause SUBSCRIPTION_ID --if-revision 2
python3 packages/enotify/enotify.py subscription resume SUBSCRIPTION_ID --if-revision 3
python3 packages/enotify/enotify.py subscription deliveries SUBSCRIPTION_ID
python3 packages/enotify/enotify.py status
```

Create and update read each JSON spec from a file or from `-` (stdin). Only one spec may use stdin in a command. Mutating existing subscriptions requires an optimistic revision. Retry and release operate on explicit reservation IDs; exhausted `one` reservations remain selected and paused until an operator retries or releases them.

## Persistence and delivery semantics

`Store.open()` enables WAL and applies every unapplied `migrations/NNN_name.sql` file in order. Occurrences are deduplicated by provider/source/occurrence identity. Each delivery has a reservation, deterministic delivery key, lease, attempts, accepted receipt or dead letter. No database transaction spans provider I/O.

For `one`, a partial unique index admits only one open reservation. Retryable failure keeps that occurrence selected; exhaustion pauses the subscription and fails closed. Accepted delivery finishes it. For `all`, every occurrence gets its own reservation; an exhausted occurrence is dead-lettered while the subscription continues. A provider result arriving after pause, update, or delete is retained as `accepted_late` and cannot resurrect or finish the subscription.

External services may still duplicate a send if they cannot deduplicate the stable delivery key and a crash occurs after external acceptance but before the local receipt commit. This package makes no exactly-once claim.

## Deploy and undeploy scaffolding

```bash
npx nx deploy enotify
npx nx undeploy enotify
```

Deploy creates only the private state directory and an `ENOTIFY_MANAGED` marker; it starts no service. Undeploy removes only that marker and preserves databases, logs, backups, and unrelated configuration. No live deployment is performed by this PR.

## Validation

```bash
npx nx test enotify
npx nx lint enotify
```

The tests cover strict provider schemas, role separation, repeatable migrations, optimistic revisions, redaction/idempotent replay, concurrent single-winner reservation, lease recovery, late results, `one` retry/exhaustion/release, and `all` continuation.
# Buzz typing transition events

The event provider `buzz/typing-transitions` is a version-1, role-safe event
source for one community/channel/author. Its optional `ttl` is a positive
integer and defaults to 8 seconds. It emits only `started` and `stopped`
transitions; refresh ticks extend the semantic deadline without emitting.

```json
{
  "provider": "buzz",
  "event_type": "typing-transitions",
  "schema_version": 1,
  "match": {
    "community": "community-id",
    "channel": "channel-id",
    "author": "author-pubkey",
    "ttl": 8,
    "history_limit": 1000,
    "direction": "started"
  }
}
```

Expiry is emitted at `last_tick_created_at + ttl`, even when no new relay
event arrives. The provider exposes a due deadline to the worker scheduler;
observation never blocks waiting for expiry. Typing ticks are ephemeral: the
provider supervises `buzz-server events subscribe --community ... --filter
'{"kinds":[20002],"authors":[...],"#h":[...]}'` as JSONL and consumes
`event` records through the initial `eose`. It never falls back to
`buzz messages get`. One bounded, reconnecting stream is shared per
community/channel/author; TTL-specific projections remain isolated in the
durable store. Reconnects use the Buzz Server managed overlap and do not
synthesize stops; persisted deadlines continue to drive expiry. The default
owner identity is resolved by Buzz Server, so no additional enotify identity
setting is required. `buzz/channel-events` is unchanged.
