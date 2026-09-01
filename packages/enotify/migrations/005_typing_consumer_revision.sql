ALTER TABLE typing_consumers ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE typing_consumers ADD COLUMN cursor_occurrence_id TEXT NOT NULL DEFAULT '';
