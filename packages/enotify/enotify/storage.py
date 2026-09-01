from __future__ import annotations
import json, sqlite3, uuid
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
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, frequency TEXT NOT NULL,
          event_json TEXT NOT NULL, notification_json TEXT NOT NULL, state TEXT NOT NULL, reason TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS occurrences(id TEXT PRIMARY KEY, source TEXT NOT NULL, observed_at TEXT NOT NULL,
          cursor TEXT, payload_json TEXT, UNIQUE(source,id));
        CREATE TABLE IF NOT EXISTS one_reservations(subscription_id TEXT PRIMARY KEY, occurrence_id TEXT NOT NULL,
          revision INTEGER NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notification_attempts(subscription_id TEXT NOT NULL, occurrence_id TEXT NOT NULL,
          attempt INTEGER NOT NULL, delivery_key TEXT NOT NULL, outcome TEXT, error TEXT, created_at TEXT NOT NULL,
          PRIMARY KEY(subscription_id,occurrence_id,attempt));
        CREATE TABLE IF NOT EXISTS accepted_deliveries(subscription_id TEXT NOT NULL, occurrence_id TEXT NOT NULL,
          delivery_id TEXT NOT NULL, accepted_at TEXT NOT NULL, PRIMARY KEY(subscription_id,occurrence_id));""")
        self.db.commit()
    def close(self):
        if self.db: self.db.close()
    def create(self, frequency, event, notification):
        if frequency not in ("one","all"): raise ValueError("frequency must be one or all")
        sid=str(uuid.uuid4()); stamp=now()
        self.db.execute("INSERT INTO subscriptions VALUES(?,?,?,?,?,?,?,?,?)",(sid,1,frequency,canonical_json(event.envelope()),canonical_json(notification.envelope()),"active",None,stamp,stamp)); self.db.commit()
        return self.get(sid)
    def _row(self,row):
        if not row: raise KeyError("subscription not found")
        return {"id":row["id"],"revision":row["revision"],"frequency":row["frequency"],"event_trigger":json.loads(row["event_json"]),"notification_address":json.loads(row["notification_json"]),"state":row["state"],"reason":row["reason"],"created_at":row["created_at"],"updated_at":row["updated_at"]}
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
    def accepted(self,sid,occurrence_id,delivery_id):
        row=self.get(sid); self.db.execute("BEGIN")
        self.db.execute("INSERT OR IGNORE INTO accepted_deliveries VALUES(?,?,?,?)",(sid,occurrence_id,delivery_id,now()))
        if row["frequency"]=="one": self.db.execute("UPDATE subscriptions SET state='finished',revision=revision+1,updated_at=? WHERE id=? AND state='active'",(now(),sid))
        self.db.execute("UPDATE one_reservations SET state='accepted' WHERE subscription_id=? AND occurrence_id=?",(sid,occurrence_id)); self.db.commit()
