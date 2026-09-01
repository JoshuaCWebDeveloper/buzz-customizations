CREATE TABLE typing_projections (
  provider TEXT NOT NULL,
  source TEXT NOT NULL,
  active INTEGER NOT NULL CHECK (active IN (0,1)),
  last_tick_at INTEGER,
  last_tick_id TEXT,
  expires_at INTEGER,
  cursor TEXT,
  last_transition_id TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (provider, source)
);
