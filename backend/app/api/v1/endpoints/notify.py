"""
多渠道通知配置 API

- GET  /notify/config   脱敏配置（Settings UI）
- PUT  /notify/config   持久化 + 版本自增 + 热重载；空密钥沿用旧值
- POST /notify/test     向指定渠道同步发送测试事件，返回成功/错误

存储 / 版本化与 broker_config / protections 一致。
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.redis import get_redis
from app.notify import store
from app.notify.channels.telegram import send_telegram_sync
from app.notify.channels.webhook import send_webhook_sync
from app.notify.config import (
    ChannelConfig,
    ChannelType,
    NotifyConfig,
    NotifyConfigStatus,
    NotifyTestRequest,
    NotifyTestResponse,
    mask_secret,
    to_status,
)
from app.notify.dispatcher import set_active_config
from app.notify.events import NotifyEvent, render_event

router = APIRouter()


@router.get("/config", response_model=NotifyConfigStatus)
async def get_notify_config_endpoint(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> NotifyConfigStatus:
    """获取脱敏后的通知配置。"""
    config = await store.load_config(redis)
    return to_status(config)


@router.put("/config", response_model=NotifyConfigStatus)
async def update_notify_config(
    body: NotifyConfig,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> NotifyConfigStatus:
    """持久化通知配置（空密钥沿用旧值）并热重载。"""
    merged = await store.save_config(redis, body)
    set_active_config(merged)
    return to_status(merged)


@router.post("/test", response_model=NotifyTestResponse)
async def test_channel(
    body: NotifyTestRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> NotifyTestResponse:
    """向指定渠道同步发送一条合成测试事件。"""
    config = await store.load_config(redis)
    channel = config.get_channel(body.channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel not found: {body.channel_id}",
        )

    event = _build_test_event(body, channel)
    rendered = render_event(event, channel)

    # 同步 httpx 调用放到线程池，避免阻塞事件循环
    if channel.type == ChannelType.TELEGRAM and channel.telegram is not None:
        return await asyncio.to_thread(
            _run_telegram_test, body.channel_id, channel, rendered.text
        )
    if channel.type == ChannelType.WEBHOOK and channel.webhook is not None:
        return await asyncio.to_thread(
            _run_webhook_test, body.channel_id, channel, rendered.payload
        )

    return NotifyTestResponse(
        ok=False, channel_id=body.channel_id, error="渠道配置不完整"
    )


# ── 工具 ──────────────────────────────────────────────────────

def _build_test_event(body: NotifyTestRequest, channel: ChannelConfig) -> NotifyEvent:
    return NotifyEvent(
        type=body.event_type,
        title=f"测试事件 · {channel.name or channel.type.value}",
        symbol="AAPL",
        market="US",
        payload={"note": "这是一条 QuantBot 测试通知", "test": True},
    )


def _run_telegram_test(
    channel_id: str, channel: ChannelConfig, text: str
) -> NotifyTestResponse:
    tg = channel.telegram
    assert tg is not None
    if not tg.bot_token:
        return NotifyTestResponse(
            ok=False, channel_id=channel_id, error="未配置 bot_token"
        )
    result = send_telegram_sync(
        text,
        token=tg.bot_token,
        chat_id=tg.chat_id,
        parse_mode=tg.parse_mode,
    )
    if result["ok"]:
        return NotifyTestResponse(
            ok=True,
            channel_id=channel_id,
            detail=f"已发送到 chat {tg.chat_id}（token {mask_secret(tg.bot_token)}）",
        )
    return NotifyTestResponse(
        ok=False, channel_id=channel_id, error=result.get("error") or "发送失败"
    )


def _run_webhook_test(
    channel_id: str, channel: ChannelConfig, payload: dict
) -> NotifyTestResponse:
    wh = channel.webhook
    assert wh is not None
    result = send_webhook_sync(
        payload,
        url=wh.url,
        format=wh.format.value,
        timeout=wh.timeout_seconds,
        retries=wh.retries,
        retry_delay=wh.retry_delay_seconds,
        secret_header=wh.secret_header,
        secret_value=wh.secret_value,
    )
    if result["ok"]:
        return NotifyTestResponse(
            ok=True,
            channel_id=channel_id,
            detail=f"HTTP {result.get('http_status')} · {result.get('attempts')} 次尝试 · {wh.url}",
        )
    return NotifyTestResponse(
        ok=False, channel_id=channel_id, error=result.get("error") or "发送失败"
    )
