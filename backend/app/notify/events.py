"""
通知事件 DTO 与渲染。

生产者构建 NotifyEvent 并调用 dispatch_event。渲染分两路：
- Telegram → 格式化文本（HTML/Markdown），复用 send_risk_alert 的 emoji/标签风格。
- Webhook  → dict，format=raw 时包装为 {"data": "<text>"}。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.notify.config import (
    ChannelConfig,
    NotifyEventType,
    WebhookFormat,
)

_MAX_TELEGRAM_LEN = 2000

# 事件类型 → emoji + 中文标签
_EVENT_LABELS: dict[NotifyEventType, tuple[str, str]] = {
    NotifyEventType.TRADE_FILL: ("✅", "成交"),
    NotifyEventType.ORDER_REJECT: ("⛔", "订单拒绝"),
    NotifyEventType.PNL_UPDATE: ("💰", "盈亏更新"),
    NotifyEventType.POSITION: ("📊", "持仓变动"),
    NotifyEventType.DAILY_SUMMARY: ("📅", "每日汇总"),
    NotifyEventType.RISK_ALERT: ("⚠️", "风控告警"),
    NotifyEventType.PROTECTION: ("🛡️", "防护熔断"),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class NotifyEvent:
    type: NotifyEventType
    title: str
    symbol: Optional[str] = None
    market: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class RenderedMessage:
    """渲染结果：Telegram 用 text，Webhook 用 payload。"""

    text: str
    payload: dict[str, Any]


def _escape_markdown(text: str) -> str:
    """转义 Markdown 敏感字符（参考 jesse _format_msg 对 _ 的处理）。"""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


def _telegram_text(event: NotifyEvent, channel: ChannelConfig) -> str:
    emoji, label = _EVENT_LABELS.get(event.type, ("🔔", "通知"))
    parse_mode = channel.telegram.parse_mode if channel.telegram else "HTML"

    lines: list[str] = []
    if parse_mode == "HTML":
        lines.append(f"{emoji} <b>QuantBot · {label}</b>")
    else:
        lines.append(f"{emoji} *QuantBot · {label}*")
    lines.append(event.title)

    if event.symbol:
        loc = event.symbol + (f" ({event.market})" if event.market else "")
        lines.append(f"标的: {loc}")

    for key, value in event.payload.items():
        lines.append(f"{key}: {value}")

    text = "\n".join(str(line) for line in lines)
    if parse_mode == "Markdown":
        text = _escape_markdown(text)
    if len(text) > _MAX_TELEGRAM_LEN:
        text = text[: _MAX_TELEGRAM_LEN - 1] + "…"
    return text


def _webhook_payload(event: NotifyEvent, channel: ChannelConfig) -> dict[str, Any]:
    base: dict[str, Any] = {
        "event": event.type.value,
        "title": event.title,
        "symbol": event.symbol,
        "market": event.market,
        "timestamp": event.created_at.isoformat(),
        **event.payload,
    }
    fmt = channel.webhook.format if channel.webhook else WebhookFormat.JSON
    if fmt == WebhookFormat.RAW:
        # raw 模式下 send_webhook 取 payload["data"]
        text = _telegram_text(event, channel)
        return {"data": text}
    return base


def render_event(event: NotifyEvent, channel: ChannelConfig) -> RenderedMessage:
    return RenderedMessage(
        text=_telegram_text(event, channel),
        payload=_webhook_payload(event, channel),
    )
