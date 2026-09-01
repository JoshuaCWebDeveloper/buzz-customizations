#!/usr/bin/env python3
"""JSON-only subscription CLI."""
import argparse, json, os
from pathlib import Path
from enotify.models import EventTriggerSpec, NotificationAddressSpec
from enotify.providers.events import default_registry as event_registry
from enotify.providers.notifications import default_registry as notification_registry
from enotify.storage import Store, Conflict

def main(argv=None):
    parser=argparse.ArgumentParser(prog="enotify"); parser.add_argument("--db",default=os.environ.get("ENOTIFY_DB",str(Path.home()/".local/state/enotify/enotify.db")))
    sub=parser.add_subparsers(dest="command",required=True); p=sub.add_parser("provider"); p.add_argument("action",choices=("list",))
    s=sub.add_parser("subscription"); ss=s.add_subparsers(dest="action",required=True)
    c=ss.add_parser("create"); c.add_argument("--frequency",required=True); c.add_argument("--event",required=True); c.add_argument("--notification",required=True)
    for action in ("list","get","pause","resume","delete","status"):
        q=ss.add_parser(action); q.add_argument("id",nargs="?"); q.add_argument("--if-revision",type=int)
    u=ss.add_parser("update"); u.add_argument("id"); u.add_argument("--frequency"); u.add_argument("--event"); u.add_argument("--notification"); u.add_argument("--if-revision",type=int)
    a=parser.parse_args(argv)
    if a.command=="provider": print(json.dumps({"events":event_registry().describe(),"notifications":notification_registry().describe()},sort_keys=True)); return 0
    store=Store(Path(a.db)); store.open()
    try:
        if a.action=="create":
            e=json.loads(a.event); n=json.loads(a.notification)
            event_provider=event_registry().get(e["provider"],e["event_type"])
            notification_provider=notification_registry().get(n["provider"],n["notification_type"])
            match=event_provider.validate_config(e["match"],e["schema_version"])
            address=notification_provider.validate_config(n["address"],n["schema_version"])
            item=store.create(a.frequency,EventTriggerSpec(e["provider"],e["event_type"],e["schema_version"],match),NotificationAddressSpec(n["provider"],n["notification_type"],n["schema_version"],address))
        elif a.action=="list": item=store.list()
        elif a.action in ("get","status"): item=store.get(a.id)
        elif a.action in ("pause","resume","delete"): item=store.transition(a.id,a.action,a.if_revision)
        elif a.action=="update":
            old=store.get(a.id)
            event=EventTriggerSpec(**{**old["event_trigger"],"match":old["event_trigger"]["match"]}) if not a.event else None
            notification=NotificationAddressSpec(**{**old["notification_address"],"address":old["notification_address"]["address"]}) if not a.notification else None
            if a.event:
                value=json.loads(a.event); provider=event_registry().get(value["provider"],value["event_type"])
                event=EventTriggerSpec(value["provider"],value["event_type"],value["schema_version"],provider.validate_config(value["match"],value["schema_version"]))
            if a.notification:
                value=json.loads(a.notification); provider=notification_registry().get(value["provider"],value["notification_type"])
                notification=NotificationAddressSpec(value["provider"],value["notification_type"],value["schema_version"],provider.validate_config(value["address"],value["schema_version"]))
            item=store.update(a.id,a.frequency,event,notification,a.if_revision)
        else: raise ValueError("unsupported action")
        print(json.dumps(item,sort_keys=True)); return 0
    except (ValueError,KeyError,Conflict) as exc: parser.error(str(exc)); return 2
    finally: store.close()
if __name__=="__main__": raise SystemExit(main())
