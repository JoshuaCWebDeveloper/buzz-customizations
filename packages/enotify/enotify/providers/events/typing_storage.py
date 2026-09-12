"""Typed repository for Buzz typing projection and consumer state."""
from __future__ import annotations

import json
from typing import Any, Callable

from .interface import EventOccurrence


def typing_source(match: dict[str, Any]) -> str:
    return json.dumps({"author": match["author"], "channel": match["channel"], "community": match["community"],
                       "event_type": "typing-transitions", "history_limit": match.get("history_limit", 1000),
                       "provider": "buzz", "ttl": match.get("ttl", 8)}, sort_keys=True, separators=(",", ":"))


class BuzzTypingStorageExtension:
    provider = "buzz"
    capability = "typing-transitions"

    @staticmethod
    def _source(event: Any) -> str:
        return typing_source(dict(event.match))

    def on_create(self, tx: Any, subscription_id: str, event: Any, stamp: str) -> None:
        source = self._source(event)
        checkpoint = tx.db.execute("SELECT cursor FROM provider_checkpoints WHERE provider=? AND source=?", ("buzz", source)).fetchone()
        tx.db.execute("INSERT INTO typing_consumers(subscription_id,source,eligible_after,cursor,updated_at,revision,cursor_occurrence_id) VALUES(?,?,?,?,?,?,?)", (subscription_id, source, stamp, int(checkpoint[0]) if checkpoint else 0, stamp, 1, ""))

    def on_update(self, tx: Any, old: dict[str, Any], event: Any | None, revision: int) -> None:
        old_typing = old["event_trigger"].get("provider") == self.provider and old["event_trigger"].get("event_type") == self.capability
        new_typing = (event.provider == self.provider and event.event_type == self.capability) if event else old_typing
        if event and new_typing:
            source = self._source(event)
            tx.db.execute("DELETE FROM typing_consumers WHERE subscription_id=?", (old["id"],))
            checkpoint = tx.db.execute("SELECT cursor FROM provider_checkpoints WHERE provider=? AND source=?", ("buzz", source)).fetchone()
            stamp = tx.now()
            tx.db.execute("INSERT INTO typing_consumers(subscription_id,source,eligible_after,cursor,updated_at,revision,cursor_occurrence_id) VALUES(?,?,?,?,?,?,?)", (old["id"], source, stamp, int(checkpoint[0]) if checkpoint else 0, stamp, revision, ""))
        elif event and old_typing:
            tx.db.execute("DELETE FROM typing_consumers WHERE subscription_id=?", (old["id"],))
        elif old_typing:
            source = self._source(type("Event", (), {"match": old["event_trigger"]["match"]})())
            tx.db.execute("UPDATE typing_consumers SET revision=? WHERE subscription_id=? AND source=?", (revision, old["id"], source))

    def on_transition(self, tx: Any, subscription: dict[str, Any], action: str, revision: int) -> None:
        if action == "pause":
            tx.db.execute("DELETE FROM typing_consumers WHERE subscription_id=?", (subscription["id"],))
        elif action == "resume":
            event = subscription["event_trigger"]
            if event.get("provider") == self.provider and event.get("event_type") == self.capability:
                source = typing_source(event["match"])
                checkpoint = tx.db.execute("SELECT cursor FROM provider_checkpoints WHERE provider=? AND source=?", ("buzz", source)).fetchone()
                stamp = tx.now()
                tx.db.execute("INSERT INTO typing_consumers(subscription_id,source,eligible_after,cursor,updated_at,revision,cursor_occurrence_id) VALUES(?,?,?,?,?,?,?)", (subscription["id"], source, stamp, int(checkpoint[0]) if checkpoint else 0, stamp, revision, ""))


class BuzzTypingRepository:
    provider = "buzz"
    capability = "typing-transitions"

    def __init__(self, store: Any):
        self.store = store

    def projection(self, provider: str, source: str) -> dict[str, Any] | None:
        row = self.store.db.execute("SELECT * FROM typing_projections WHERE provider=? AND source=?", (provider, source)).fetchone()
        return dict(row) if row else None

    def deadline(self) -> int | None:
        row = self.store.db.execute("SELECT MIN(expires_at) FROM typing_projections WHERE active=1").fetchone()
        return row[0] if row and row[0] is not None else None

    def ensure_consumer(self, subscription_id: str, source: str) -> None:
        with self.store.transaction() as tx:
            existing = tx.db.execute("SELECT 1 FROM typing_consumers WHERE subscription_id=? AND source=?", (subscription_id, source)).fetchone()
            if existing:
                return
            checkpoint = tx.db.execute("SELECT cursor FROM provider_checkpoints WHERE provider=? AND source=?", ("buzz", source)).fetchone()
            subscription = tx.db.execute("SELECT revision FROM subscriptions WHERE id=?", (subscription_id,)).fetchone()
            tx.db.execute("INSERT INTO typing_consumers(subscription_id,source,eligible_after,cursor,updated_at,revision,cursor_occurrence_id) VALUES(?,?,?,?,?,?,?)", (subscription_id, source, tx.now(), int(checkpoint[0]) if checkpoint else 0, tx.now(), subscription[0], ""))

    def consumer_occurrences(self, subscription_id: str, source: str, limit: int = 1000) -> list[EventOccurrence]:
        rows = self.store.db.execute(
            """SELECT e.* FROM event_occurrences e JOIN typing_consumers c ON c.source=e.source
               JOIN subscriptions s ON s.id=c.subscription_id
               WHERE c.subscription_id=? AND e.provider='buzz' AND e.source=? AND c.revision=s.revision
               AND e.created_at>=c.eligible_after AND (CAST(e.cursor AS INTEGER)>c.cursor OR (CAST(e.cursor AS INTEGER)=c.cursor AND e.occurrence_id>c.cursor_occurrence_id))
               ORDER BY CAST(e.cursor AS INTEGER),e.occurrence_id LIMIT ?""", (subscription_id, source, limit))
        return [EventOccurrence(row["provider"], row["source"], row["occurrence_id"], row["observed_at"], row["cursor"], json.loads(row["payload_json"])) for row in rows]

    def advance_consumer(self, subscription_id: str, source: str, cursor: str | None, occurrence_id: str = "") -> None:
        if cursor is None:
            return
        with self.store.transaction() as tx:
            tx.db.execute("""UPDATE typing_consumers SET cursor_occurrence_id=CASE WHEN CAST(? AS INTEGER)>cursor THEN ? WHEN CAST(? AS INTEGER)=cursor THEN MAX(cursor_occurrence_id,?) ELSE cursor_occurrence_id END,
                       cursor=MAX(cursor,CAST(? AS INTEGER)),updated_at=? WHERE subscription_id=? AND source=?""", (cursor, occurrence_id, cursor, occurrence_id, cursor, tx.now(), subscription_id, source))

    def poll(self, provider: Any, subscription_id: str, source: str, ticks: list[dict[str, Any]], observed_at: int) -> list[EventOccurrence]:
        emitted: list[EventOccurrence] = []
        with self.store.transaction() as tx:
            row = tx.db.execute("SELECT * FROM typing_projections WHERE provider=? AND source=?", (provider.provider, source)).fetchone()
            if row is None:
                tx.db.execute("INSERT INTO typing_projections(provider,source,active,revision,updated_at) VALUES(?,?,?,?,?)", (provider.provider, source, 0, 1, tx.now()))
                row = tx.db.execute("SELECT * FROM typing_projections WHERE provider=? AND source=?", (provider.provider, source)).fetchone()
            active, expires, last_tick = bool(row["active"]), row["expires_at"], row["last_tick_at"]
            last_tick_id = row["last_tick_id"]
            if active and expires is not None and expires <= observed_at:
                occurrence = provider.transition_occurrence("stopped", expires, observed_at)
                tx.record_occurrence(occurrence)
                if provider._matches(occurrence): emitted.append(occurrence)
                active, expires = False, None
            for tick in ticks:
                tick_at = tick["created_at"]
                if tick_at + provider.config["ttl"] <= observed_at or (last_tick is not None and tick_at <= last_tick):
                    continue
                was_active = active
                active, last_tick, last_tick_id, expires = True, tick_at, tick["id"], tick_at + provider.config["ttl"]
                if not was_active:
                    occurrence = provider.transition_occurrence("started", tick_at, observed_at)
                    tx.record_occurrence(occurrence)
                    if provider._matches(occurrence): emitted.append(occurrence)
            cursor = max(int(row["cursor"] or 0), max((int(t["created_at"]) for t in ticks), default=0))
            tx.db.execute("UPDATE typing_projections SET active=?,last_tick_at=?,last_tick_id=?,expires_at=?,cursor=?,revision=revision+1,updated_at=? WHERE provider=? AND source=? AND revision=?", (int(active), last_tick, last_tick_id, expires, cursor, tx.now(), provider.provider, source, row["revision"]))
            if ticks:
                tx.checkpoint(provider.provider, source, str(cursor))
            pending = tx.db.execute("SELECT e.* FROM event_occurrences e JOIN typing_consumers c ON c.source=e.source JOIN subscriptions s ON s.id=c.subscription_id WHERE c.subscription_id=? AND e.provider=? AND e.source=? AND c.revision=s.revision AND e.created_at>=c.eligible_after AND (CAST(e.cursor AS INTEGER)>c.cursor OR (CAST(e.cursor AS INTEGER)=c.cursor AND e.occurrence_id>c.cursor_occurrence_id)) ORDER BY CAST(e.cursor AS INTEGER),e.occurrence_id", (subscription_id, provider.provider, source)).fetchall()
            for row in pending:
                occurrence = EventOccurrence(row["provider"], row["source"], row["occurrence_id"], row["observed_at"], row["cursor"], json.loads(row["payload_json"]))
                if provider._matches(occurrence):
                    emitted.append(occurrence)
        return emitted
