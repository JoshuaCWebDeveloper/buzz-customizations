PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY);
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
 delivery_id TEXT NOT NULL, accepted_at TEXT NOT NULL, PRIMARY KEY(subscription_id,occurrence_id));
CREATE TABLE IF NOT EXISTS idempotency_keys(key TEXT PRIMARY KEY, operation TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dead_letters(subscription_id TEXT NOT NULL, occurrence_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(subscription_id,occurrence_id));
CREATE TABLE IF NOT EXISTS leases(subscription_id TEXT NOT NULL, occurrence_id TEXT NOT NULL, revision INTEGER NOT NULL, expires_at TEXT NOT NULL, PRIMARY KEY(subscription_id,occurrence_id));
CREATE TABLE IF NOT EXISTS delivery_claims(subscription_id TEXT NOT NULL, occurrence_id TEXT NOT NULL,
 revision INTEGER NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(subscription_id,occurrence_id));
