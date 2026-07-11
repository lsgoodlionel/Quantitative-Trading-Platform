"""多渠道通知包（Telegram / Webhook）。"""

from __future__ import annotations

from app.notify.config import (
    ChannelConfig,
    ChannelType,
    NotifyConfig,
    NotifyEventType,
    default_notify_config,
)
from app.notify.dispatcher import dispatch_event, get_notify_config, set_active_config
from app.notify.events import NotifyEvent, render_event

__all__ = [
    "ChannelConfig",
    "ChannelType",
    "NotifyConfig",
    "NotifyEventType",
    "default_notify_config",
    "NotifyEvent",
    "render_event",
    "dispatch_event",
    "get_notify_config",
    "set_active_config",
]
