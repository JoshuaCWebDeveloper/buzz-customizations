CREATE TABLE migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE subscriptions (
  id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL CHECK (revision > 0),
  frequency TEXT NOT NULL CHECK (frequency IN ('one', 'all')),
  event_json TEXT NOT NULL,
  notification_json TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'paused', 'finished', 'dead', 'deleted')),
  reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE event_occurrences (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  source TEXT NOT NULL,
  occurrence_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  cursor TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (provider, source, occurrence_id)
);

CREATE TABLE provider_checkpoints (
  provider TEXT NOT NULL,
  source TEXT NOT NULL,
  cursor TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (provider, source)
);

CREATE TABLE delivery_reservations (
  id TEXT PRIMARY KEY,
  subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
  occurrence_row_id TEXT NOT NULL REFERENCES event_occurrences(id),
  subscription_revision INTEGER NOT NULL,
  frequency TEXT NOT NULL CHECK (frequency IN ('one', 'all')),
  delivery_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('reserved', 'sending', 'retryable', 'accepted', 'exhausted', 'dead', 'released')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (subscription_id, occurrence_row_id)
);

CREATE UNIQUE INDEX one_open_reservation
ON delivery_reservations(subscription_id)
WHERE frequency = 'one' AND state IN ('reserved', 'sending', 'retryable', 'exhausted');

CREATE TABLE delivery_leases (
  reservation_id TEXT PRIMARY KEY REFERENCES delivery_reservations(id),
  owner TEXT NOT NULL,
  expires_at REAL NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE notification_attempts (
  id TEXT PRIMARY KEY,
  reservation_id TEXT NOT NULL REFERENCES delivery_reservations(id),
  attempt INTEGER NOT NULL CHECK (attempt > 0),
  outcome TEXT NOT NULL CHECK (outcome IN ('started', 'accepted', 'accepted_late', 'retryable', 'permanent')),
  error TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE (reservation_id, attempt)
);

CREATE TABLE accepted_deliveries (
  reservation_id TEXT PRIMARY KEY REFERENCES delivery_reservations(id),
  attempt_id TEXT NOT NULL REFERENCES notification_attempts(id),
  receipt TEXT NOT NULL,
  accepted_at TEXT NOT NULL,
  late INTEGER NOT NULL DEFAULT 0 CHECK (late IN (0, 1))
);

CREATE TABLE dead_letters (
  reservation_id TEXT PRIMARY KEY REFERENCES delivery_reservations(id),
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE idempotent_mutations (
  key TEXT PRIMARY KEY,
  operation TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE audit_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  subject_id TEXT,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
