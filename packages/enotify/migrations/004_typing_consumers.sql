CREATE TABLE typing_consumers (
  subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
  source TEXT NOT NULL,
  eligible_after TEXT NOT NULL,
  cursor INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (subscription_id, source)
);
