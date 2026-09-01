# enotify

Package-owned subscription control plane and provider extension seams. The persisted API is JSON-only: event specs use provider, event_type, schema_version, and provider-validated match; notification specs use provider, notification_type, schema_version, and address.

Event and notification registries are separate role namespaces, so buzz may exist in both without ambiguous lookup. This first foundation observes external sources only and does not launch or supervise processes, alter Buzz Server, or deploy live services.

Run the enotify.py provider list command and use JSON envelopes for subscription creation. deploy.py install and uninstall are intentionally explicit scaffolding.
