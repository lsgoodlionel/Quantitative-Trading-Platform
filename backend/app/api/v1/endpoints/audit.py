"""
审计日志 API 端点

- GET /audit  分页查询审计留痕（倒序，可按 action / actor 过滤）

从 Redis stream（audit:log）读取，倒序扫描一个有界窗口后在内存分页；
stream 已由 audit_log 以 maxlen 裁剪，扫描量恒定有界。
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.audit import AUDIT_STREAM, parse_entry
from app.core.redis import get_redis

router = APIRouter()

# 单次倒序扫描的最大条目数（有界，保护内存与延迟）
_MAX_SCAN = 2000


# ── Schemas ──────────────────────────────────────────────────

class AuditRecord(BaseModel):
    id: str
    ts: str
    action: str
    actor: str
    detail: dict[str, Any] = Field(default_factory=dict)


class AuditListResponse(BaseModel):
    items: list[AuditRecord]
    total: int
    page: int
    page_size: int


# ── 端点 ─────────────────────────────────────────────────────

@router.get("", response_model=AuditListResponse)
async def list_audit_logs(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    action: Optional[str] = Query(None, description="按动作精确过滤，如 order.submit"),
    actor: Optional[str] = Query(None, description="按操作者模糊过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AuditListResponse:
    """分页查询审计日志（倒序，最新在前）。"""
    try:
        entries = await redis.xrevrange(
            AUDIT_STREAM, max="+", min="-", count=_MAX_SCAN
        )
    except Exception:
        # Redis 不可用时返回空集，不阻断 Settings 页面
        return AuditListResponse(items=[], total=0, page=page, page_size=page_size)

    records = [parse_entry(entry_id, fields) for entry_id, fields in entries]
    records = _apply_filters(records, action=action, actor=actor)

    total = len(records)
    start = (page - 1) * page_size
    page_items = records[start : start + page_size]

    return AuditListResponse(
        items=[AuditRecord(**r) for r in page_items],
        total=total,
        page=page,
        page_size=page_size,
    )


def _apply_filters(
    records: list[dict[str, Any]],
    *,
    action: Optional[str],
    actor: Optional[str],
) -> list[dict[str, Any]]:
    """按 action（精确）与 actor（模糊、忽略大小写）过滤。"""
    if action:
        records = [r for r in records if r["action"] == action]
    if actor:
        needle = actor.lower()
        records = [r for r in records if needle in r["actor"].lower()]
    return records
