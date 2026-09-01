"""Notification-provider API and built-in registry."""
from .interface import NotificationProvider, SendResult
from .registry import NotificationRegistry, default_registry

__all__ = ["NotificationProvider", "SendResult", "NotificationRegistry", "default_registry"]
