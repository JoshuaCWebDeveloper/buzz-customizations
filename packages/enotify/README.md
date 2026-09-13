# enotify

`enotify` is an independent, package-owned notification control-plane foundation. It owns its SQLite WAL database, numbered forward migrations, provider registries, lifecycle CLI, delivery ledger, tests, and explicit deploy/undeploy scaffolding. It does not import Buzz Server internals or change Buzz Server state.

## Persisted contract

Event and notification specs are JSON-only and deliberately distinct:

```json
{"provider":"github","event_type":"check","schema_version":1,"match":{"repository":"owner/repo","check":{"name":{"equals":"ci"},"status":{"in":["completed"]}},"pull_request":{"number":42},"credential_ref":{"scope":"service","name":"github"}}}
```

When `pull_request` is present, the event provider resolves that PR directly,
reads its current `head.sha`, and polls check-runs for that SHA. It does not
depend on the head appearing in the recent default-branch commit listing.

`credential_ref` is a reference, never a token. The currently supported
reference is `{ "scope": "service", "name": "github" }`. The deployed service
resolves it from its environment file using `GITHUB_TOKEN` first and
`GH_TOKEN` second; empty values are skipped. Missing or unsupported references
fail without changing subscription state, and credentials are never included
in normalized specs, persisted state, logs, or errors.

```json
{"provider":"buzz","notification_type":"message","schema_version":1,"address":{"community":"community-id","channel":"channel-id","mention":{"pubkey":"hex-or-npub","handle":"Alice"}}}
```

Buzz message addresses may also contain optional `content` text with only the
`{author}` and `{direction}` fields. `{author}` is the validated mention handle
when `mention` is configured; the typing event's Nostr author pubkey is never
used as display text. For a no-mention address, use literal text such as
`"content":"Phaeax has {direction} working"`, which renders exactly
`Phaeax has started working` or `Phaeax has stopped working`.
Templates are literal text plus those two fields; attribute access, indexing,
conversions, format specifications, and expressions are rejected during
subscription create/update validation. Without `content`, typing transitions
use the compact `Typing {direction}` default; existing addresses remain valid
without migration. Mentions are still prepended and passed as structured Buzz
CLI mentions. This structured `--mention` identity is required for
mentions-only agent wake-up; the readable `@handle` text is presentation, while
the pubkey is the delivery identity.

Providers reject unknown fields and normalize accepted values before persistence. Event and notification interfaces, registries, implementations, and tests are physically separate; `buzz/channel-events` and `buzz/message` resolve only through their role-specific registries.

The initial provider implementations are validation and extension seams. They do not yet observe Buzz, GitHub, or process events and do not publish Buzz messages. `system-process/exited` describes an already-running PID and never launches or supervises a process.

## CLI

After deployment, agents use the installed CLI directly. It defaults to the
shared worker database; `--db` or `ENOTIFY_DB` remains available for an
explicit alternate state store:

```bash
enotify provider list
enotify provider describe event github check
enotify subscription create \
  --event-spec event.json \
  --notification-spec notification.json
# Omit --frequency for the default `all`; pass `--frequency one` explicitly
# when the subscription should stop after its first accepted delivery.
# Only one active or paused `all` subscription may have the same canonical event
# and notification specs. A conflicting create fails and names the existing ID;
# `one` subscriptions may be repeated. Finished, dead, and deleted subscriptions
# do not block recreation.
enotify subscription update SUBSCRIPTION_ID \
  --if-revision 1 \
  --event-spec event.json
enotify subscription pause SUBSCRIPTION_ID --if-revision 2
enotify subscription resume SUBSCRIPTION_ID --if-revision 3
enotify subscription deliveries SUBSCRIPTION_ID
enotify status
```

Specs may be inline JSON, a JSON file path, or `-` for stdin. Version 1 is the
default schema version. This is the minimal typing-to-Buzz subscription; the
typing TTL/history limit and omitted notification mention use provider defaults:

```bash
enotify subscription create --frequency one \
  --event-spec '{"provider":"buzz","event_type":"typing-transitions","match":{"community":"COMMUNITY_ID","channel":"CHANNEL_ID","author":"AUTHOR_PUBKEY"}}' \
  --notification-spec '{"provider":"buzz","notification_type":"message","address":{"community":"COMMUNITY_ID","channel":"CHANNEL_ID","content":"Phaeax has {direction} working"}}'
```

Create and update read each JSON spec from a file or from `-` (stdin). Only one spec may use stdin in a command. Mutating existing subscriptions requires an optimistic revision. Retry and release operate on explicit reservation IDs; exhausted `one` reservations remain selected and paused until an operator retries or releases them.

## Persistence and delivery semantics

`Store.open()` enables WAL and applies every unapplied `migrations/NNN_name.sql` file in order. Occurrences are deduplicated by provider/source/occurrence identity. Active and paused `all` subscriptions are unique by canonical event and notification JSON, with SQLite enforcing the invariant for concurrent creates. Migration retires later pre-existing duplicates as deleted rows, preserving their IDs and delivery history. Each delivery has a reservation, deterministic delivery key, lease, attempts, accepted receipt or dead letter. No database transaction spans provider I/O.

For `one`, a partial unique index admits only one open reservation. Retryable failure keeps that occurrence selected; exhaustion pauses the subscription and fails closed. Accepted delivery finishes it. For `all`, every occurrence gets its own reservation; an exhausted occurrence is dead-lettered while the subscription continues. A provider result arriving after pause, update, or delete is retained as `accepted_late` and cannot resurrect or finish the subscription.

External services may still duplicate a send if they cannot deduplicate the stable delivery key and a crash occurs after external acceptance but before the local receipt commit. This package makes no exactly-once claim.

## Provider runtime and state extensions

The Worker and long-lived service operate only on the provider-agnostic runtime
contract. A runtime handle is subscription-scoped, while the runtime registry
owns one shared backend per provider observation group and releases it only after
the final handle stops. One service-level wake coordinator combines runtime
wakes and monotonic deadlines; provider event timestamps remain wall-clock data.

Provider-specific durable state belongs to a typed storage extension registered
with the provider runtime. Extensions receive a narrowly scoped Store-owned
transaction capability, so provider projection, cursor, consumer, and common
occurrence writes can commit atomically without provider I/O in the transaction.
The Buzz typing extension wraps the existing projection and consumer tables;
no migration is required for this boundary. Other stateful providers can
register their own typed repository and runtime without adding methods or
branches to the core Store or Worker.

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

The tests cover strict provider schemas, role separation, repeatable migrations, deterministic duplicate migration, canonical all-subscription conflicts, concurrent all creates, repeated `one` creates, optimistic revisions, redaction/idempotent replay, concurrent single-winner reservation, lease recovery, late results, `one` retry/exhaustion/release, and `all` continuation.
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

The live stream executable is selected from the validated `executable` match
field, then `ENOTIFY_BUZZ_EVENTS`, then `buzz-events` on the service `PATH`.
The worker passes its inherited authenticated environment to that executable;
credential values are never copied into the subscription or command line.

Expiry is emitted at `last_tick_created_at + ttl`, even when no new relay
event arrives. The provider exposes a due deadline to the worker scheduler;
observation never blocks waiting for expiry. Typing ticks are ephemeral: the
provider supervises the configured/discovered `buzz-events subscribe
--filter '{"kinds":[20002],"authors":[...],"#h":[...]}'` as JSONL and consumes
`event` records through the initial `eose`. It never falls back to
`buzz messages get`. One bounded, reconnecting stream is shared per
community/channel/author; TTL-specific projections remain isolated in the
durable store. Reconnects use the Buzz Server managed overlap and do not
synthesize stops; persisted deadlines continue to drive expiry. The default
owner identity is resolved by Buzz Server, so no additional enotify identity
setting is required. `buzz/channel-events` is unchanged.
