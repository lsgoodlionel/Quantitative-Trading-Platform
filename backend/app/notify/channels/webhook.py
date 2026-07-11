"""
Webhook 发送辅助，含指数退避重试。

参考 freqtrade/rpc/webhook.py::_send_msg 语义，新增指数退避：
- 总尝试次数 = 1 + retries。
- 第 n 次重试（1-indexed）前等待 retry_delay * 2**(n-1)，上限 30s。
- format：json → json=payload；form → data=payload；raw → data=payload["data"]。
- 尝试耗尽后记录 warning 并放弃（不向生产者抛出异常）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.notify.config import WebhookFormat

logger = logging.getLogger(__name__)

_MAX_BACKOFF_SECONDS = 30.0


def _backoff_delay(attempt_index: int, base_delay: float) -> float:
    """attempt_index 为 1-indexed 的重试序号。"""
    return min(base_delay * (2 ** (attempt_index - 1)), _MAX_BACKOFF_SECONDS)


def send_webhook_sync(
    payload: dict[str, Any],
    *,
    url: str,
    format: str = "json",
    timeout: int = 10,
    retries: int = 2,
    retry_delay: float = 1.0,
    secret_header: Optional[str] = None,
    secret_value: Optional[str] = None,
) -> dict[str, Any]:
    """
    同步 POST，带自重试退避。返回 {"ok", "http_status", "error", "attempts"}。
    """
    if not url:
        return {"ok": False, "http_status": None, "error": "webhook url missing", "attempts": 0}

    try:
        import httpx
    except ImportError:
        return {"ok": False, "http_status": None, "error": "httpx not installed", "attempts": 0}

    headers: dict[str, str] = {}
    if secret_header and secret_value:
        headers[secret_header] = secret_value

    fmt = _normalize_format(format)
    request_kwargs = _build_request_kwargs(fmt, payload, headers)

    total_attempts = 1 + max(0, retries)
    last_error: Optional[str] = None

    for attempt in range(total_attempts):
        if attempt > 0:
            time.sleep(_backoff_delay(attempt, retry_delay))
        try:
            resp = httpx.post(url, timeout=timeout, **request_kwargs)
            resp.raise_for_status()
            return {
                "ok": True,
                "http_status": resp.status_code,
                "error": None,
                "attempts": attempt + 1,
            }
        except Exception as e:  # noqa: BLE001 - 循环内自处理重试
            last_error = str(e)
            logger.debug("Webhook attempt %d/%d failed: %s", attempt + 1, total_attempts, last_error)

    logger.warning("Webhook giving up after %d attempts: %s", total_attempts, last_error)
    return {"ok": False, "http_status": None, "error": last_error, "attempts": total_attempts}


def _normalize_format(fmt: str) -> WebhookFormat:
    try:
        return WebhookFormat(fmt)
    except ValueError:
        return WebhookFormat.JSON


def _build_request_kwargs(
    fmt: WebhookFormat, payload: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any]:
    if fmt == WebhookFormat.FORM:
        return {"data": payload, "headers": headers}
    if fmt == WebhookFormat.RAW:
        raw_headers = {**headers, "Content-Type": "text/plain"}
        return {"data": payload.get("data", ""), "headers": raw_headers}
    return {"json": payload, "headers": headers}
