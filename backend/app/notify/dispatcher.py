"""
通知分发器。

dispatch_event(event)：加载 NotifyConfig，按 enabled + 事件订阅筛选渠道，
渲染并将发送任务入队到 Celery（.delay，fire-and-forget）。

配置读取采用同步 Redis + 版本号缓存，避免在下单热路径中 await。
单渠道失败不影响其他渠道或调用方。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.notify.config import (
    ChannelConfig,
    ChannelType,
    NotifyConfig,
    NotifyEventType,
    default_notify_config,
)
from app.notify.events import NotifyEvent, render_event

logger = logging.getLogger(__name__)

_CONFIG_KEY = "notify:config"
_CONFIG_VERSION_KEY = "notify:config:version"
_DATA_FIELD = "data"

# ── 同步配置缓存（按版本号失效） ──────────────────────────────
_cached_config: Optional[NotifyConfig] = None
_cached_version: Optional[str] = None
_sync_client = None


def _get_sync_client():
    global _sync_client
    if _sync_client is None:
        try:
            import redis

            from app.core.config import settings

            _sync_client = redis.Redis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
        except Exception:
            logger.exception("Failed to init sync redis client for notify")
            _sync_client = None
    return _sync_client


def set_active_config(config: NotifyConfig) -> None:
    """PUT 后由端点调用，刷新进程内缓存。"""
    global _cached_config, _cached_version
    _cached_config = config


def get_notify_config() -> NotifyConfig:
    """获取当前通知配置（Redis 版本号变化时重载）。"""
    global _cached_config, _cached_version
    client = _get_sync_client()
    if client is None:
        return _cached_config or default_notify_config()

    try:
        version = client.get(_CONFIG_VERSION_KEY)
    except Exception:
        return _cached_config or default_notify_config()

    if _cached_config is not None and version == _cached_version:
        return _cached_config

    try:
        raw = client.hget(_CONFIG_KEY, _DATA_FIELD)
        config = (
            NotifyConfig.model_validate(json.loads(raw))
            if raw
            else default_notify_config()
        )
    except Exception:
        logger.exception("Failed to load notify config, using default")
        config = default_notify_config()

    _cached_config = config
    _cached_version = version
    return config


# ── 分发 ──────────────────────────────────────────────────────

def _should_suppress(event: NotifyEvent, config: NotifyConfig) -> bool:
    """pnl_update 低于阈值时抑制。"""
    if event.type != NotifyEventType.PNL_UPDATE:
        return False
    pnl = event.payload.get("realized_pnl")
    if pnl is None:
        return False
    try:
        return abs(float(pnl)) < config.min_pnl_notify_abs
    except (TypeError, ValueError):
        return False


def _enqueue_channel(event: NotifyEvent, channel: ChannelConfig) -> bool:
    """将单渠道发送入队。返回是否成功入队。"""
    from app.tasks.notify import send_telegram, send_webhook

    rendered = render_event(event, channel)
    try:
        if channel.type == ChannelType.TELEGRAM and channel.telegram is not None:
            tg = channel.telegram
            send_telegram.delay(
                rendered.text,
                token=tg.bot_token,
                chat_id=tg.chat_id,
                parse_mode=tg.parse_mode,
            )
            return True
        if channel.type == ChannelType.WEBHOOK and channel.webhook is not None:
            wh = channel.webhook
            send_webhook.delay(
                rendered.payload,
                url=wh.url,
                format=wh.format.value,
                timeout=wh.timeout_seconds,
                retries=wh.retries,
                retry_delay=wh.retry_delay_seconds,
                secret_header=wh.secret_header,
                secret_value=wh.secret_value,
            )
            return True
    except Exception:
        logger.exception("Failed to enqueue notify for channel %s", channel.id)
    return False


def dispatch_event(event: NotifyEvent, config: Optional[NotifyConfig] = None) -> dict:
    """
    分发一个通知事件到所有匹配渠道（fire-and-forget）。
    返回 {"dispatched": n, "skipped": bool} 供调试。
    """
    cfg = config or get_notify_config()
    if not cfg.is_active:
        return {"dispatched": 0, "skipped": True}
    if _should_suppress(event, cfg):
        return {"dispatched": 0, "skipped": True}

    dispatched = 0
    for channel in cfg.channels:
        if not channel.enabled:
            continue
        if event.type not in channel.events:
            continue
        if _enqueue_channel(event, channel):
            dispatched += 1

    return {"dispatched": dispatched, "skipped": False}
