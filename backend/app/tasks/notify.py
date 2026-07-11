"""
通知任务模块（Celery transport 层）

支持:
- Telegram Bot 推送
- Webhook POST（含指数退避重试）

由 app.notify.dispatcher 依据存储配置驱动；也保留对 settings.* 的
向后兼容回退（已废弃）。send_risk_alert 为便捷封装，构建 NotifyEvent
并调用 dispatch_event。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.tasks.notify.send_telegram", max_retries=3, default_retry_delay=30)
def send_telegram(
    message: str,
    *,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: str = "HTML",
    timeout: int = 10,
) -> dict:
    """发送 Telegram 消息。未显式传入 token/chat_id 时回退到 settings。"""
    if not token or not chat_id:
        from app.core.config import settings

        token = token or settings.telegram_bot_token
        chat_id = chat_id or settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug("Telegram not configured, skipping notification")
        return {"status": "skipped", "reason": "not_configured"}

    from app.notify.channels.telegram import send_telegram_sync

    result = send_telegram_sync(
        message, token=token, chat_id=chat_id, parse_mode=parse_mode, timeout=timeout
    )
    if not result["ok"]:
        return {"status": "error", "reason": result.get("error")}
    return {"status": "ok", "message_id": result.get("message_id")}


@shared_task(name="app.tasks.notify.send_webhook", max_retries=2, default_retry_delay=60)
def send_webhook(
    payload: dict,
    *,
    url: Optional[str] = None,
    format: str = "json",
    timeout: int = 10,
    retries: int = 2,
    retry_delay: float = 1.0,
    secret_header: Optional[str] = None,
    secret_value: Optional[str] = None,
) -> dict:
    """向 Webhook URL 发送 POST（helper 内含退避重试）。未传 url 回退 settings。"""
    if not url:
        from app.core.config import settings

        url = settings.notify_webhook_url

    if not url:
        return {"status": "skipped", "reason": "not_configured"}

    from app.notify.channels.webhook import send_webhook_sync

    result = send_webhook_sync(
        payload,
        url=url,
        format=format,
        timeout=timeout,
        retries=retries,
        retry_delay=retry_delay,
        secret_header=secret_header,
        secret_value=secret_value,
    )
    if not result["ok"]:
        return {"status": "error", "reason": result.get("error"), "attempts": result.get("attempts")}
    return {"status": "ok", "http_status": result.get("http_status")}


@shared_task(name="app.tasks.notify.send_risk_alert")
def send_risk_alert(violation_message: str, severity: str, portfolio_value: float) -> dict:
    """风控告警便捷封装：构建 NotifyEvent 并经 dispatch_event 分发。"""
    from app.notify.config import NotifyEventType
    from app.notify.dispatcher import dispatch_event
    from app.notify.events import NotifyEvent

    event = NotifyEvent(
        type=NotifyEventType.RISK_ALERT,
        title=f"风控告警 · {severity}",
        payload={
            "severity": severity,
            "message": violation_message,
            "portfolio_value": round(portfolio_value, 2),
        },
    )
    result = dispatch_event(event)
    return {"status": "dispatched", **result}


def emit_event(
    event_type: str,
    title: str,
    *,
    symbol: Optional[str] = None,
    market: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> dict:
    """
    生产者便捷入口：同步构建并分发一个事件（内部 .delay 入队实际发送）。

    OMS / 策略引擎可直接调用，不阻塞热路径。
    """
    from app.notify.config import NotifyEventType
    from app.notify.dispatcher import dispatch_event
    from app.notify.events import NotifyEvent

    try:
        et = NotifyEventType(event_type)
    except ValueError:
        logger.warning("Unknown notify event type: %s", event_type)
        return {"dispatched": 0, "skipped": True}

    event = NotifyEvent(
        type=et,
        title=title,
        symbol=symbol,
        market=market,
        payload=payload or {},
    )
    return dispatch_event(event)
