"""Injectable occurrence/attempt orchestration; external I/O is outside DB transactions."""
class Worker:
    def __init__(self, store, event_provider, notification_provider):
        self.store,self.event_provider,self.notification_provider=store,event_provider,notification_provider
    def process(self, subscription, render):
        for occurrence in self.event_provider.observe(None):
            self.store.record_occurrence(occurrence)
            if subscription["frequency"]=="one" and not self.store.reserve_one(subscription["id"],occurrence.occurrence_id): continue
            current=self.store.get(subscription["id"])
            if not self.store.claim(subscription["id"],occurrence.occurrence_id,current["revision"],1): continue
            try: delivery=self.notification_provider.send(render(occurrence),f'{subscription["id"]}/{occurrence.occurrence_id}')
            except Exception as exc:
                self.store.record_attempt(subscription["id"],occurrence.occurrence_id,1,"retryable",str(exc)[:500]); continue
            self.store.accepted(subscription["id"],occurrence.occurrence_id,delivery.delivery_id)
