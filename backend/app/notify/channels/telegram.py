"""Telegram 发送辅助（薄封装，供 Celery 任务与测试端点调用）。"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_sync(
    message: str,
    *,
    token: str,
    chat_id: str,
    parse_mode: str = "HTML",
    timeout: int = 10,
) -> dict[str, Any]:
    """
    同步发送 Telegram 消息（单次尝试；Celery 层负责重试）。

    返回 {"ok": bool, "message_id": int|None, "error": str|None}。
    """
    if not token or not chat_id:
        return {"ok": False, "message_id": None, "error": "telegram not configured"}

    try:
        import httpx

        url = _TELEGRAM_API.format(token=token)
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": parse_mode},
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        msg_id: Optional[int] = result.get("message_id")
        return {"ok": True, "message_id": msg_id, "error": None}
    except Exception as e:  # noqa: BLE001 - 记录并返回错误，不向调用方抛出
        err = _extract_error(e)
        logger.warning("Telegram send failed: %s", err)
        return {"ok": False, "message_id": None, "error": err}


def _extract_error(exc: Exception) -> str:
    """尽力从 Telegram 错误响应中提取 description。"""
    text = str(exc)
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get("description"):
                return str(body["description"])
        except Exception:
            pass
    return text
