from __future__ import annotations
import json, sqlite3, uuid
from pathlib import Path
from datetime import datetime, timezone
from .models import EventTriggerSpec, NotificationAddressSpec, canonical_json

def now(): return datetime.now(timezone.utc).isoformat()
class Conflict(RuntimeError): pass

class Store:
    def __init__(self, path): self.path=path; self.db=None
    def open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        migration=Path(__file__).parents[1]/"migrations/001_initial.sql"
        self.db.executescript(migration.read_text(encoding="utf-8"))
        self.db.execute("INSERT OR IGNORE INTO migrations(version) VALUES(1)")
        self.db.commit()
    def close(self):
        if self.db: self.db.close()
    def create(self, frequency, event, notification):
        if frequency not in ("one","all"): raise ValueError("frequency must be one or all")
        sid=str(uuid.uuid4()); stamp=now()
        self.db.execute("INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?,?)",(sid,1,frequency,canonical_json(event.envelope()),canonical_json(notification.envelope()),"active",None,stamp,stamp)); self.db.commit()
        return self.get(sid)
    def update(self, sid, frequency=None, event=None, notification=None, expected=None):
        old=self.get(sid)
        if expected is not None and old["revision"] != expected: raise Conflict("revision conflict")
        frequency=frequency or old["frequency"]
        if frequency not in ("one","all"): raise ValueError("frequency must be one or all")
        event_json=canonical_json(event.envelope()) if event else canonical_json(old["event_trigger"])
        notification_json=canonical_json(notification.envelope()) if notification else canonical_json(old["notification_address"])
        cur=self.db.execute("UPDATE subscriptions SET frequency=?,event_json=?,notification_json=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",(frequency,event_json,notification_json,now(),sid,old["revision"]))
        if cur.rowcount!=1: raise Conflict("revision conflict")
        self.db.commit(); return self.get(sid)
    def redact(self, value):
        if isinstance(value, dict):
            return {k: ("[redacted]" if k.lower() in ("secret","token","password","secret_value") else self.redact(v)) for k,v in value.items()}
        if isinstance(value, list): return [self.redact(v) for v in value]
        return value
    def mutate_idempotent(self, key, operation, action):
        if self.db.in_transaction: self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        row=self.db.execute("SELECT result_json FROM idempotency_keys WHERE key=?",(key,)).fetchone()
        if row: self.db.commit(); return json.loads(row[0])
        result=self.redact(action()); encoded=json.dumps(result,sort_keys=True)
        self.db.execute("INSERT INTO idempotency_keys VALUES(?,?,?,?)",(key,operation,encoded,now())); self.db.commit(); return json.loads(encoded)
    def _row(self,row):
        if not row: raise KeyError("subscription not found")
        return self.redact({"id":row["id"],"revision":row["revision"],"frequency":row["frequency"],"event_trigger":json.loads(row["event_json"]),"notification_address":json.loads(row["notification_json"]),"state":row["state"],"reason":row["reason"],"created_at":row["created_at"],"updated_at":row["updated_at"]})
    def get(self,sid): return self._row(self.db.execute("SELECT * FROM subscriptions WHERE id=?",(sid,)).fetchone())
    def list(self): return [self._row(r) for r in self.db.execute("SELECT * FROM subscriptions ORDER BY created_at")]
    def transition(self,sid,action,expected=None):
        target={"pause":"paused","resume":"active","delete":"deleted"}[action]; old=self.get(sid)
        if expected is not None and old["revision"] != expected: raise Conflict("revision conflict")
        if action=="resume" and old["state"]!="paused": raise ValueError("only paused subscriptions can resume")
        stamp=now(); cur=self.db.execute("UPDATE subscriptions SET state=?,revision=revision+1,updated_at=?,reason=? WHERE id=? AND revision=?",(target,stamp,None if target=="active" else action,sid,old["revision"]))
        if cur.rowcount!=1: raise Conflict("revision conflict")
        self.db.commit(); return self.get(sid)
    def record_occurrence(self, occurrence):
        self.db.execute("INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?,?)",(occurrence.occurrence_id,occurrence.source,occurrence.observed_at,occurrence.cursor,json.dumps(occurrence.payload or {},sort_keys=True))); self.db.commit()
    def reserve_one(self,sid,occurrence_id):
        row=self.get(sid)
        if row["frequency"]!="one" or row["state"]!="active": return False
        cur=self.db.execute("INSERT OR IGNORE INTO one_reservations VALUES(?,?,?,?,?)",(sid,occurrence_id,row["revision"],"reserved",now())); self.db.commit(); return cur.rowcount==1
    def record_attempt(self,sid,occurrence_id,attempt,outcome,error=None):
        self.db.execute("INSERT OR IGNORE INTO notification_attempts VALUES(?,?,?,?,?,?,?)",(sid,occurrence_id,attempt,f"{sid}/{occurrence_id}",outcome,error,now())); self.db.commit()
    def claim(self,sid,occurrence_id,revision,attempt,ttl_seconds=300):
        self.db.execute("BEGIN IMMEDIATE")
        sub=self.db.execute("SELECT state,revision,frequency FROM subscriptions WHERE id=?",(sid,)).fetchone()
        occurrence=self.db.execute("SELECT 1 FROM occurrences WHERE id=?",(occurrence_id,)).fetchone()
        if not sub or sub["state"]!="active" or sub["revision"]!=revision or not occurrence:
            self.db.rollback(); return False
        if sub["frequency"]=="one":
            reservation=self.db.execute("SELECT state,revision FROM one_reservations WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)).fetchone()
            if not reservation or reservation["state"]!="reserved" or reservation["revision"]!=revision: self.db.rollback(); return False
        accepted=self.db.execute("SELECT 1 FROM accepted_deliveries WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)).fetchone()
        if accepted: self.db.rollback(); return False
        existing=self.db.execute("SELECT revision,state FROM delivery_claims WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)).fetchone()
        if existing and existing["state"] not in ("retryable","expired"): self.db.rollback(); return False
        lease=self.db.execute("SELECT expires_at FROM leases WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)).fetchone()
        if lease and float(lease["expires_at"]) > datetime.now(timezone.utc).timestamp(): self.db.rollback(); return False
        self.db.execute("INSERT OR REPLACE INTO delivery_claims VALUES(?,?,?,?,?)",(sid,occurrence_id,revision,"sending",now()))
        self.db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,?,?)",(sid,occurrence_id,revision,datetime.now(timezone.utc).timestamp()+ttl_seconds))
        self.db.execute("INSERT OR IGNORE INTO notification_attempts VALUES(?,?,?,?,?,?,?)",(sid,occurrence_id,attempt,f"{sid}/{occurrence_id}","started",None,now()))
        self.db.commit(); return True
    def accepted_for(self,sid,occurrence_id):
        return self.db.execute("SELECT 1 FROM accepted_deliveries WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)).fetchone() is not None
    def reclaim_expired(self):
        now_epoch=datetime.now(timezone.utc).timestamp()
        expired=[tuple(r) for r in self.db.execute("SELECT subscription_id,occurrence_id FROM leases WHERE expires_at<=?",(now_epoch,))]
        for sid,oid in expired: self.db.execute("UPDATE delivery_claims SET state='expired' WHERE subscription_id=? AND occurrence_id=? AND state='sending'",(sid,oid))
        cur=self.db.execute("DELETE FROM leases WHERE expires_at<=?",(now_epoch,)); self.db.commit(); return cur.rowcount
    def accepted(self,sid,occurrence_id,delivery_id,revision):
        self.db.execute("BEGIN IMMEDIATE")
        row=self.db.execute("SELECT * FROM subscriptions WHERE id=?",(sid,)).fetchone()
        occurrence=self.db.execute("SELECT 1 FROM occurrences WHERE id=?",(occurrence_id,)).fetchone()
        attempt=self.db.execute("SELECT 1 FROM notification_attempts WHERE subscription_id=? AND occurrence_id=? AND outcome='started'",(sid,occurrence_id)).fetchone()
        claim=self.db.execute("SELECT state,revision FROM delivery_claims WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)).fetchone()
        if not row or not occurrence or not attempt or not claim: self.db.rollback(); raise Conflict("accepted delivery lacks observed occurrence, claim, and started attempt")
        if row["state"]!="active" or row["revision"]!=revision or claim["state"]!="sending" or claim["revision"]!=revision:
            self.db.rollback(); raise Conflict("subscription changed during delivery")
        if row["frequency"]=="one":
            reservation=self.db.execute("SELECT * FROM one_reservations WHERE subscription_id=? AND occurrence_id=? AND state='reserved' AND revision=?",(sid,occurrence_id,row["revision"])).fetchone()
            if not reservation: self.db.rollback(); raise Conflict("accepted delivery lacks current reservation")
        self.db.execute("INSERT OR IGNORE INTO accepted_deliveries VALUES(?,?,?,?)",(sid,occurrence_id,delivery_id,now()))
        if row["frequency"]=="one":
            cur=self.db.execute("UPDATE subscriptions SET state='finished',revision=revision+1,updated_at=? WHERE id=? AND state='active' AND revision=?",(now(),sid,row["revision"]))
            if cur.rowcount!=1: self.db.rollback(); raise Conflict("subscription revision changed")
        self.db.execute("UPDATE one_reservations SET state='accepted' WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)); self.db.commit()
        self.db.execute("UPDATE delivery_claims SET state='accepted' WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id))
        self.db.execute("DELETE FROM leases WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)); self.db.commit()
    def failed_attempt(self,sid,occurrence_id,attempt,error,max_attempts=3):
        self.db.execute("UPDATE notification_attempts SET outcome='retryable',error=? WHERE subscription_id=? AND occurrence_id=? AND attempt=?",(error,sid,occurrence_id,attempt))
        self.db.execute("UPDATE delivery_claims SET state='retryable' WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id))
        self.db.execute("DELETE FROM leases WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)); self.db.commit()
        if attempt >= max_attempts:
            row=self.get(sid)
            if row["frequency"]=="one": self.exhaust_one(sid,occurrence_id)
            return False
        return True
    def exhaust_one(self,sid,occurrence_id,reason="delivery_exhausted"):
        row=self.get(sid)
        cur=self.db.execute("UPDATE subscriptions SET state='paused',revision=revision+1,reason=?,updated_at=? WHERE id=? AND state='active'",(reason,now(),sid))
        if cur.rowcount!=1: raise Conflict("subscription is not active")
        self.db.execute("INSERT OR IGNORE INTO dead_letters VALUES(?,?,?,?)",(sid,occurrence_id,reason,now())); self.db.commit(); return self.get(sid)
    def release_one(self,sid,occurrence_id):
        self.get(sid); cur=self.db.execute("UPDATE one_reservations SET state='released' WHERE subscription_id=? AND occurrence_id=? AND state IN ('reserved','dead')",(sid,occurrence_id))
        if cur.rowcount!=1: return False
        self.db.execute("DELETE FROM dead_letters WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id))
        self.db.execute("UPDATE subscriptions SET state='active',reason=NULL,revision=revision+1,updated_at=? WHERE id=? AND state='paused'",(now(),sid))
        self.db.commit(); return True
    def deliveries(self,sid,limit=100):
        self.get(sid)
        return [dict(r) for r in self.db.execute("SELECT * FROM notification_attempts WHERE subscription_id=? ORDER BY created_at DESC LIMIT ?",(sid,limit))]
