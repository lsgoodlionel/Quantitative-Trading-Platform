"""
通知中心 API（V3 G5 §2.5）

- GET    /notify/inbox              通知历史（时间倒序）+ 未读数
- POST   /notify/inbox/{id}/read    标记单条已读 / 未读
- POST   /notify/inbox/read-all     全部标记已读
- DELETE /notify/inbox/{id}         删除单条
- DELETE /notify/inbox              清空收件箱

挂载在 notify.py 的 /notify 前缀下，故无需改动共享的 router.py。
"""

from __future__ import annotations

from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.core.redis import get_redis
from app.notify import inbox

router = APIRouter()

_DEFAULT_LIMIT = 50


# ── Schemas ──────────────────────────────────────────────────

class NotificationOut(BaseModel):
    id: str
    type: str
    title: str
    symbol: str | None = None
    market: str | None = None
    payload: dict = Field(default_factory=dict)
    is_read: bool = False
    created_at: float = 0.0


class InboxResponse(BaseModel):
    items: list[NotificationOut]
    unread: int


class MarkReadRequest(BaseModel):
    is_read: bool = True


def _to_out(notification: inbox.Notification) -> NotificationOut:
    return NotificationOut(
        id=notification.id,
        type=notification.type,
        title=notification.title,
        symbol=notification.symbol,
        market=notification.market,
        payload=notification.payload or {},
        is_read=notification.is_read,
        created_at=notification.created_at,
    )


# ── 端点 ─────────────────────────────────────────────────────

@router.get("/inbox", response_model=InboxResponse)
async def list_inbox(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=inbox.MAX_LIST_LIMIT),
    unread_only: bool = Query(default=False),
) -> InboxResponse:
    """通知历史（时间倒序）。unread_only=true 仅返回未读。"""
    items = await inbox.list_notifications(redis, limit=limit, unread_only=unread_only)
    unread = await inbox.count_unread(redis)
    return InboxResponse(items=[_to_out(n) for n in items], unread=unread)


@router.post("/inbox/read-all")
async def mark_all_read(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """把全部未读标记为已读。"""
    updated = await inbox.mark_all_read(redis)
    return {"updated": updated}


@router.post("/inbox/{notification_id}/read", response_model=NotificationOut)
async def mark_read(
    notification_id: str,
    body: MarkReadRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> NotificationOut:
    """标记单条通知已读 / 未读。"""
    updated = await inbox.mark_read(redis, notification_id, is_read=body.is_read)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification not found: {notification_id}",
        )
    return _to_out(updated)


@router.delete("/inbox/{notification_id}")
async def delete_notification(
    notification_id: str,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """删除单条通知。"""
    existed = await inbox.delete_notification(redis, notification_id)
    if not existed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification not found: {notification_id}",
        )
    return {"deleted": notification_id}


@router.delete("/inbox")
async def clear_inbox(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """清空收件箱。"""
    return {"deleted": await inbox.clear_all(redis)}
