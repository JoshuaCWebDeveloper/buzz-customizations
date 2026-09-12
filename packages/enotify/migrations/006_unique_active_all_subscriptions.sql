-- Retire later duplicate active/paused all rows without deleting history.
-- The earliest created row wins; id breaks timestamp ties deterministically.
UPDATE subscriptions
SET state='deleted', reason='duplicate_frequency_all',
    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
WHERE frequency='all'
  AND state IN ('active','paused')
  AND EXISTS (
    SELECT 1 FROM subscriptions AS keeper
    WHERE keeper.frequency='all'
      AND keeper.state IN ('active','paused')
      AND keeper.event_json=subscriptions.event_json
      AND keeper.notification_json=subscriptions.notification_json
      AND (keeper.created_at < subscriptions.created_at
           OR (keeper.created_at = subscriptions.created_at AND keeper.id < subscriptions.id))
  );

CREATE UNIQUE INDEX active_all_subscription_specs
ON subscriptions(event_json, notification_json)
WHERE frequency='all' AND state IN ('active','paused');
