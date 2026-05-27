"""
通知任务模块

支持:
- Telegram Bot 推送
- Webhook POST
- 邮件（SMTP）

在策略触发重要事件（风控熔断、重大盈亏）时，由 RiskEngine / OMS 异步触发。
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.tasks.notify.send_telegram", max_retries=3, default_retry_delay=30)
def send_telegram(message: str) -> dict:
    """发送 Telegram 消息。"""
    from app.core.config import settings
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        logger.debug("Telegram not configured, skipping notification")
        return {"status": "skipped", "reason": "not_configured"}

    try:
        import httpx
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
        return {"status": "ok", "message_id": resp.json().get("result", {}).get("message_id")}
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        raise


@shared_task(name="app.tasks.notify.send_webhook", max_retries=2, default_retry_delay=60)
def send_webhook(payload: dict) -> dict:
    """向配置的 Webhook URL 发送 POST 请求。"""
    from app.core.config import settings
    url = settings.notify_webhook_url
    if not url:
        return {"status": "skipped", "reason": "not_configured"}

    try:
        import httpx
        resp = httpx.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return {"status": "ok", "http_status": resp.status_code}
    except Exception as e:
        logger.error("Webhook send failed: %s", e)
        raise


@shared_task(name="app.tasks.notify.send_risk_alert")
def send_risk_alert(violation_message: str, severity: str, portfolio_value: float) -> dict:
    """风控告警通知（同时推送 Telegram + Webhook）。"""
    msg = (
        f"⚠️ <b>QuantBot 风控告警</b>\n"
        f"级别: {severity}\n"
        f"说明: {violation_message}\n"
        f"当前净值: {portfolio_value:,.2f}"
    )
    send_telegram.delay(msg)
    send_webhook.delay({
        "event": "risk_alert",
        "severity": severity,
        "message": violation_message,
        "portfolio_value": portfolio_value,
    })
    return {"status": "dispatched"}
