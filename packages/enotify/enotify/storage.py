"""Package-owned SQLite persistence, migrations, and delivery ledger."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator
import uuid

from .models import EventTriggerSpec, NotificationAddressSpec, canonical_json
from .providers.events import EventOccurrence


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_epoch() -> float:
    return datetime.now(timezone.utc).timestamp()


class Conflict(RuntimeError):
    pass


class Store:
    def __init__(self, path: Path, migrations: Path | None = None):
        self.path = Path(path)
        self.migrations = migrations or Path(__file__).parents[1] / "migrations"
        self.db: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def _connection(self) -> sqlite3.Connection:
        if self.db is None:
            raise RuntimeError("store is not open")
        return self.db

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = self._connection()
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        else:
            db.commit()

    @staticmethod
    def _sql_statements(script: str) -> Iterator[str]:
        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                if statement.strip():
                    yield statement
                statement = ""
        if statement.strip():
            raise RuntimeError("incomplete migration statement")

    def _migrate(self) -> None:
        db = self._connection()
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='migrations'"
        ).fetchone()
        applied = set()
        if exists:
            applied = {row[0] for row in db.execute("SELECT version FROM migrations")}
        files = sorted(self.migrations.glob("[0-9][0-9][0-9]_*.sql"))
        if not files:
            raise RuntimeError("no enotify migrations found")
        for migration in files:
            version = int(migration.name.split("_", 1)[0])
            if version in applied:
                continue
            with self._transaction() as transaction:
                for statement in self._sql_statements(migration.read_text(encoding="utf-8")):
                    transaction.execute(statement)
                transaction.execute(
                    "INSERT INTO migrations(version,name,applied_at) VALUES(?,?,?)",
                    (version, migration.name, now()),
                )

    @staticmethod
    def redact(value: Any) -> Any:
        sensitive = {"secret", "token", "password", "secret_value", "credential", "private_key"}
        if isinstance(value, dict):
            return {
                key: "[redacted]" if key.lower() in sensitive else Store.redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [Store.redact(item) for item in value]
        return value

    @staticmethod
    def _subscription(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise KeyError("subscription not found")
        return Store.redact(
            {
                "id": row["id"],
                "revision": row["revision"],
                "frequency": row["frequency"],
                "event_trigger": json.loads(row["event_json"]),
                "notification_address": json.loads(row["notification_json"]),
                "state": row["state"],
                "reason": row["reason"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    def audit(self, action: str, subject_id: str | None, detail: Any, actor: str = "cli") -> None:
        self._connection().execute(
            "INSERT INTO audit_records(actor,action,subject_id,detail_json,created_at) VALUES(?,?,?,?,?)",
            (actor, action, subject_id, canonical_json(self.redact(detail)), now()),
        )

    def create(
        self,
        frequency: str,
        event: EventTriggerSpec,
        notification: NotificationAddressSpec,
    ) -> dict[str, Any]:
        if frequency not in ("one", "all"):
            raise ValueError("frequency must be one or all")
        subscription_id = str(uuid.uuid4())
        stamp = now()
        with self._transaction() as db:
            db.execute(
                "INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    subscription_id,
                    1,
                    frequency,
                    canonical_json(event.envelope()),
                    canonical_json(notification.envelope()),
                    "active",
                    None,
                    stamp,
                    stamp,
                ),
            )
            self.audit("subscription.create", subscription_id, {"frequency": frequency})
        return self.get(subscription_id)

    def get(self, subscription_id: str) -> dict[str, Any]:
        return self._subscription(
            self._connection().execute(
                "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
            ).fetchone()
        )

    def list(self, state: str | None = None) -> list[dict[str, Any]]:
        if state is None:
            rows = self._connection().execute("SELECT * FROM subscriptions ORDER BY created_at")
        else:
            rows = self._connection().execute(
                "SELECT * FROM subscriptions WHERE state=? ORDER BY created_at", (state,)
            )
        return [self._subscription(row) for row in rows]

    def update(
        self,
        subscription_id: str,
        expected: int,
        frequency: str | None = None,
        event: EventTriggerSpec | None = None,
        notification: NotificationAddressSpec | None = None,
    ) -> dict[str, Any]:
        old = self.get(subscription_id)
        frequency = frequency or old["frequency"]
        if frequency not in ("one", "all"):
            raise ValueError("frequency must be one or all")
        event_json = canonical_json(event.envelope() if event else old["event_trigger"])
        notification_json = canonical_json(
            notification.envelope() if notification else old["notification_address"]
        )
        with self._transaction() as db:
            cursor = db.execute(
                """UPDATE subscriptions
                   SET frequency=?,event_json=?,notification_json=?,revision=revision+1,updated_at=?
                   WHERE id=? AND revision=? AND state NOT IN ('finished','dead','deleted')""",
                (frequency, event_json, notification_json, now(), subscription_id, expected),
            )
            if cursor.rowcount != 1:
                raise Conflict("revision conflict or terminal subscription")
            self.audit("subscription.update", subscription_id, {"from_revision": expected})
        return self.get(subscription_id)

    def transition(self, subscription_id: str, action: str, expected: int) -> dict[str, Any]:
        rules = {
            "pause": ({"active"}, "paused"),
            "resume": ({"paused"}, "active"),
            "delete": ({"active", "paused", "finished", "dead"}, "deleted"),
        }
        if action not in rules:
            raise ValueError("unknown subscription transition")
        sources, target = rules[action]
        placeholders = ",".join("?" for _ in sources)
        with self._transaction() as db:
            cursor = db.execute(
                f"""UPDATE subscriptions SET state=?,revision=revision+1,updated_at=?,reason=?
                    WHERE id=? AND revision=? AND state IN ({placeholders})""",
                (target, now(), None if target == "active" else action, subscription_id, expected, *sorted(sources)),
            )
            if cursor.rowcount != 1:
                raise Conflict("revision conflict or invalid state transition")
            self.audit(f"subscription.{action}", subscription_id, {"from_revision": expected})
        return self.get(subscription_id)

    def mutate_idempotent(
        self,
        key: str,
        operation: str,
        request: Any,
        action: Callable[[], Any],
    ) -> Any:
        request_hash = hashlib.sha256(canonical_json(request).encode()).hexdigest()
        with self._transaction() as db:
            row = db.execute(
                "SELECT operation,request_hash,result_json FROM idempotent_mutations WHERE key=?",
                (key,),
            ).fetchone()
            if row:
                if row["operation"] != operation or row["request_hash"] != request_hash:
                    raise Conflict("idempotency key reused for a different mutation")
                return json.loads(row["result_json"])
            result = self.redact(action())
            encoded = canonical_json(result)
            db.execute(
                "INSERT INTO idempotent_mutations VALUES(?,?,?,?,?)",
                (key, operation, request_hash, encoded, now()),
            )
            return json.loads(encoded)

    def record_occurrence(self, occurrence: EventOccurrence) -> dict[str, Any]:
        row_id = str(uuid.uuid4())
        with self._transaction() as db:
            db.execute(
                """INSERT OR IGNORE INTO event_occurrences
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    row_id,
                    occurrence.provider,
                    occurrence.source,
                    occurrence.occurrence_id,
                    occurrence.observed_at,
                    occurrence.cursor,
                    canonical_json(occurrence.payload or {}),
                    now(),
                ),
            )
            row = db.execute(
                "SELECT * FROM event_occurrences WHERE provider=? AND source=? AND occurrence_id=?",
                (occurrence.provider, occurrence.source, occurrence.occurrence_id),
            ).fetchone()
            if occurrence.cursor is not None:
                checkpoint = db.execute(
                    "SELECT cursor FROM provider_checkpoints WHERE provider=? AND source=?",
                    (occurrence.provider, occurrence.source),
                ).fetchone()
                if checkpoint is None or str(occurrence.cursor) >= str(checkpoint["cursor"]):
                    db.execute(
                        """INSERT INTO provider_checkpoints VALUES(?,?,?,?)
                           ON CONFLICT(provider,source) DO UPDATE SET cursor=excluded.cursor,updated_at=excluded.updated_at""",
                        (occurrence.provider, occurrence.source, occurrence.cursor, now()),
                    )
        return dict(row)

    def checkpoint(self, provider: str, source: str) -> str | None:
        row = self._connection().execute(
            "SELECT cursor FROM provider_checkpoints WHERE provider=? AND source=?",
            (provider, source),
        ).fetchone()
        return row["cursor"] if row else None

    def reserve(self, subscription_id: str, occurrence_row_id: str) -> dict[str, Any] | None:
        with self._transaction() as db:
            subscription = db.execute(
                "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
            ).fetchone()
            occurrence = db.execute(
                "SELECT 1 FROM event_occurrences WHERE id=?", (occurrence_row_id,)
            ).fetchone()
            if not subscription or subscription["state"] != "active" or not occurrence:
                return None
            existing = db.execute(
                "SELECT * FROM delivery_reservations WHERE subscription_id=? AND occurrence_row_id=?",
                (subscription_id, occurrence_row_id),
            ).fetchone()
            if existing:
                return dict(existing)
            reservation_id = str(uuid.uuid4())
            delivery_key = hashlib.sha256(
                f"{subscription_id}:{subscription['revision']}:{occurrence_row_id}".encode()
            ).hexdigest()
            try:
                db.execute(
                    "INSERT INTO delivery_reservations VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        reservation_id,
                        subscription_id,
                        occurrence_row_id,
                        subscription["revision"],
                        subscription["frequency"],
                        delivery_key,
                        "reserved",
                        now(),
                        now(),
                    ),
                )
            except sqlite3.IntegrityError:
                return None
            row = db.execute(
                "SELECT * FROM delivery_reservations WHERE id=?", (reservation_id,)
            ).fetchone()
            return dict(row)

    def claim(self, reservation_id: str, owner: str, ttl_seconds: int = 300) -> dict[str, Any] | None:
        with self._transaction() as db:
            row = db.execute(
                """SELECT r.*,s.state AS subscription_state,s.revision AS current_revision
                   FROM delivery_reservations r JOIN subscriptions s ON s.id=r.subscription_id
                   WHERE r.id=?""",
                (reservation_id,),
            ).fetchone()
            if (
                not row
                or row["subscription_state"] != "active"
                or row["current_revision"] != row["subscription_revision"]
                or row["state"] not in ("reserved", "retryable")
            ):
                return None
            lease = db.execute(
                "SELECT expires_at FROM delivery_leases WHERE reservation_id=?", (reservation_id,)
            ).fetchone()
            if lease and lease["expires_at"] > now_epoch():
                return None
            attempt = db.execute(
                "SELECT COALESCE(MAX(attempt),0)+1 FROM notification_attempts WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()[0]
            attempt_id = str(uuid.uuid4())
            db.execute(
                "INSERT OR REPLACE INTO delivery_leases VALUES(?,?,?,?)",
                (reservation_id, owner, now_epoch() + ttl_seconds, now()),
            )
            db.execute(
                "UPDATE delivery_reservations SET state='sending',updated_at=? WHERE id=?",
                (now(), reservation_id),
            )
            db.execute(
                "INSERT INTO notification_attempts VALUES(?,?,?,?,?,?,?)",
                (attempt_id, reservation_id, attempt, "started", None, now(), None),
            )
            return {"reservation": dict(row), "attempt": attempt, "attempt_id": attempt_id}

    def reclaim_expired(self) -> int:
        with self._transaction() as db:
            expired = [
                row[0]
                for row in db.execute(
                    "SELECT reservation_id FROM delivery_leases WHERE expires_at<=?", (now_epoch(),)
                )
            ]
            for reservation_id in expired:
                db.execute(
                    "UPDATE delivery_reservations SET state='retryable',updated_at=? WHERE id=? AND state='sending'",
                    (now(), reservation_id),
                )
                db.execute(
                    """UPDATE notification_attempts SET outcome='retryable',error='lease expired',completed_at=?
                       WHERE reservation_id=? AND outcome='started'""",
                    (now(), reservation_id),
                )
            db.execute("DELETE FROM delivery_leases WHERE expires_at<=?", (now_epoch(),))
            return len(expired)

    def heartbeat(self, reservation_id: str, owner: str, ttl_seconds: int = 300) -> bool:
        with self._transaction() as db:
            cursor = db.execute(
                """UPDATE delivery_leases SET expires_at=?,updated_at=?
                   WHERE reservation_id=? AND owner=? AND expires_at>?""",
                (now_epoch() + ttl_seconds, now(), reservation_id, owner, now_epoch()),
            )
            return cursor.rowcount == 1

    def accepted(self, reservation_id: str, attempt: int, receipt: str) -> str:
        with self._transaction() as db:
            row = db.execute(
                """SELECT r.*,s.state AS subscription_state,s.revision AS current_revision
                   FROM delivery_reservations r JOIN subscriptions s ON s.id=r.subscription_id
                   WHERE r.id=?""",
                (reservation_id,),
            ).fetchone()
            attempt_row = db.execute(
                "SELECT * FROM notification_attempts WHERE reservation_id=? AND attempt=? AND outcome='started'",
                (reservation_id, attempt),
            ).fetchone()
            if not row or not attempt_row or row["state"] != "sending":
                raise Conflict("accepted delivery lacks a sending reservation and started attempt")
            late = row["subscription_state"] != "active" or row["current_revision"] != row["subscription_revision"]
            outcome = "accepted_late" if late else "accepted"
            db.execute(
                "UPDATE notification_attempts SET outcome=?,completed_at=? WHERE id=?",
                (outcome, now(), attempt_row["id"]),
            )
            db.execute(
                "INSERT OR IGNORE INTO accepted_deliveries VALUES(?,?,?,?,?)",
                (reservation_id, attempt_row["id"], receipt, now(), int(late)),
            )
            db.execute(
                "UPDATE delivery_reservations SET state='accepted',updated_at=? WHERE id=?",
                (now(), reservation_id),
            )
            db.execute("DELETE FROM delivery_leases WHERE reservation_id=?", (reservation_id,))
            if row["frequency"] == "one" and not late:
                cursor = db.execute(
                    """UPDATE subscriptions SET state='finished',revision=revision+1,reason=NULL,updated_at=?
                       WHERE id=? AND state='active' AND revision=?""",
                    (now(), row["subscription_id"], row["subscription_revision"]),
                )
                if cursor.rowcount != 1:
                    raise Conflict("subscription revision changed")
            return outcome

    def failed(
        self,
        reservation_id: str,
        attempt: int,
        outcome: str,
        error: str,
        max_attempts: int,
    ) -> str:
        if outcome not in ("retryable", "permanent"):
            raise ValueError("invalid failure outcome")
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM delivery_reservations WHERE id=?", (reservation_id,)
            ).fetchone()
            attempt_row = db.execute(
                "SELECT id FROM notification_attempts WHERE reservation_id=? AND attempt=? AND outcome='started'",
                (reservation_id, attempt),
            ).fetchone()
            if not row or not attempt_row or row["state"] != "sending":
                raise Conflict("failed delivery lacks a sending reservation and started attempt")
            db.execute(
                "UPDATE notification_attempts SET outcome=?,error=?,completed_at=? WHERE id=?",
                (outcome, error[:500], now(), attempt_row["id"]),
            )
            db.execute("DELETE FROM delivery_leases WHERE reservation_id=?", (reservation_id,))
            exhausted = outcome == "permanent" or attempt >= max_attempts
            if not exhausted:
                db.execute(
                    "UPDATE delivery_reservations SET state='retryable',updated_at=? WHERE id=?",
                    (now(), reservation_id),
                )
                return "retryable"
            terminal_state = "exhausted" if row["frequency"] == "one" else "dead"
            db.execute(
                "UPDATE delivery_reservations SET state=?,updated_at=? WHERE id=?",
                (terminal_state, now(), reservation_id),
            )
            reason = "delivery_permanent" if outcome == "permanent" else "delivery_exhausted"
            db.execute(
                "INSERT OR REPLACE INTO dead_letters VALUES(?,?,?)",
                (reservation_id, reason, now()),
            )
            if row["frequency"] == "one":
                db.execute(
                    """UPDATE subscriptions SET state='paused',revision=revision+1,reason=?,updated_at=?
                       WHERE id=? AND state='active' AND revision=?""",
                    (reason, now(), row["subscription_id"], row["subscription_revision"]),
                )
            return terminal_state

    def request_retry(self, subscription_id: str, reservation_id: str, expected: int) -> dict[str, Any]:
        with self._transaction() as db:
            subscription = db.execute(
                "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
            ).fetchone()
            reservation = db.execute(
                "SELECT * FROM delivery_reservations WHERE id=? AND subscription_id=?",
                (reservation_id, subscription_id),
            ).fetchone()
            if not subscription or subscription["revision"] != expected or not reservation:
                raise Conflict("revision conflict or reservation not found")
            if reservation["state"] not in ("exhausted", "dead"):
                raise Conflict("reservation is not exhausted")
            new_revision = expected
            if subscription["state"] == "paused":
                new_revision += 1
                db.execute(
                    "UPDATE subscriptions SET state='active',revision=?,reason=NULL,updated_at=? WHERE id=?",
                    (new_revision, now(), subscription_id),
                )
            db.execute(
                "UPDATE delivery_reservations SET state='retryable',subscription_revision=?,updated_at=? WHERE id=?",
                (new_revision, now(), reservation_id),
            )
            db.execute("DELETE FROM dead_letters WHERE reservation_id=?", (reservation_id,))
        return self.reservation(reservation_id)

    def release(
        self,
        subscription_id: str,
        reservation_id: str,
        expected: int,
        resume: bool,
    ) -> dict[str, Any]:
        with self._transaction() as db:
            subscription = db.execute(
                "SELECT * FROM subscriptions WHERE id=?", (subscription_id,)
            ).fetchone()
            reservation = db.execute(
                "SELECT * FROM delivery_reservations WHERE id=? AND subscription_id=? AND frequency='one'",
                (reservation_id, subscription_id),
            ).fetchone()
            if not subscription or subscription["revision"] != expected or not reservation:
                raise Conflict("revision conflict or reservation not found")
            if reservation["state"] not in ("reserved", "retryable", "exhausted"):
                raise Conflict("reservation cannot be released")
            db.execute(
                "UPDATE delivery_reservations SET state='released',updated_at=? WHERE id=?",
                (now(), reservation_id),
            )
            db.execute("DELETE FROM delivery_leases WHERE reservation_id=?", (reservation_id,))
            db.execute("DELETE FROM dead_letters WHERE reservation_id=?", (reservation_id,))
            if resume:
                if subscription["state"] != "paused":
                    raise Conflict("release --resume requires a paused subscription")
                db.execute(
                    "UPDATE subscriptions SET state='active',revision=revision+1,reason=NULL,updated_at=? WHERE id=?",
                    (now(), subscription_id),
                )
        return self.get(subscription_id)

    def reservation(self, reservation_id: str) -> dict[str, Any]:
        row = self._connection().execute(
            "SELECT * FROM delivery_reservations WHERE id=?", (reservation_id,)
        ).fetchone()
        if not row:
            raise KeyError("reservation not found")
        return dict(row)

    def deliveries(self, subscription_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self.get(subscription_id)
        rows = self._connection().execute(
            """SELECT r.id AS reservation_id,r.state,r.delivery_key,a.attempt,a.outcome,a.error,
                      a.started_at,a.completed_at,d.receipt,d.accepted_at,d.late
               FROM delivery_reservations r
               LEFT JOIN notification_attempts a ON a.reservation_id=r.id
               LEFT JOIN accepted_deliveries d ON d.reservation_id=r.id
               WHERE r.subscription_id=? ORDER BY r.created_at DESC,a.attempt DESC LIMIT ?""",
            (subscription_id, limit),
        )
        return [self.redact(dict(row)) for row in rows]

    def status(self) -> dict[str, Any]:
        db = self._connection()
        return {
            "journal_mode": db.execute("PRAGMA journal_mode").fetchone()[0],
            "migration_version": db.execute("SELECT COALESCE(MAX(version),0) FROM migrations").fetchone()[0],
            "subscriptions": db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0],
            "open_reservations": db.execute(
                "SELECT COUNT(*) FROM delivery_reservations WHERE state IN ('reserved','sending','retryable','exhausted')"
            ).fetchone()[0],
            "dead_letters": db.execute("SELECT COUNT(*) FROM dead_letters").fetchone()[0],
        }
