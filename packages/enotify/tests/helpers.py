from __future__ import annotations

from enotify.models import EventTriggerSpec, NotificationAddressSpec


def specs():
    return (
        EventTriggerSpec(
            "github",
            "check",
            1,
            {"repository": "owner/repo", "check": {"name": {"equals": "ci"}}},
        ),
        NotificationAddressSpec(
            "buzz", "message", 1, {"community": "community", "channel": "channel"}
        ),
    )
