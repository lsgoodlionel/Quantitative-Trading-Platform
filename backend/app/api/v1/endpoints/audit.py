"""
审计日志 API 端点

- GET /audit  分页查询审计留痕（倒序，可按 action / actor 过滤）

**读优先走 Redis**（`audit:log` stream，倒序扫描一个有界窗口后在内存分页；
stream 已由 `audit_log` 以 maxlen 裁剪，扫描量恒定有界）。
**Redis 不可用或没命中时回落 Postgres 持久层**（`app/data/storage/audit_log.py`）——
此前 Redis 一挂就返回空集，那和「审计被删了」在用户眼里没有区别。

⚠️ **审计记录不可变：本模块只有 GET。** 刻意不提供 PUT / PATCH / DELETE ——
一个能被删除的审计日志，在真正需要它的那一刻就是空的。
（`tests/test_audit_store.py` 用反射断言了这一点，别顺手加写端点。）
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AUDIT_STREAM, parse_entry
from app.core.database import get_db
from app.core.redis import get_redis
from app.data.storage.audit_log import PostgresAuditLogStore

router = APIRouter()

logger = logging.getLogger(__name__)

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
    source: str = Field("redis", description="本次结果来自哪一层：redis / postgres / none")


# ── 端点 ─────────────────────────────────────────────────────

@router.get("", response_model=AuditListResponse)
async def list_audit_logs(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
    session: Annotated[AsyncSession, Depends(get_db)],
    action: str | None = Query(None, description="按动作精确过滤，如 order.submit"),
    actor: str | None = Query(None, description="按操作者模糊过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> AuditListResponse:
    """分页查询审计日志（倒序，最新在前）。Redis 优先，失败/空结果回落 Postgres。"""
    records = await _read_redis(redis, action=action, actor=actor)

    if records:
        total = len(records)
        start = (page - 1) * page_size
        return AuditListResponse(
            items=[AuditRecord(**r) for r in records[start : start + page_size]],
            total=total,
            page=page,
            page_size=page_size,
            source="redis",
        )

    return await _read_postgres(
        session, action=action, actor=actor, page=page, page_size=page_size
    )


async def _read_redis(
    redis: aioredis.Redis, *, action: str | None, actor: str | None
) -> list[dict[str, Any]]:
    """读 Redis 快速查询层；不可用时返回空列表（由调用方回落 Postgres）。"""
    try:
        entries = await redis.xrevrange(AUDIT_STREAM, max="+", min="-", count=_MAX_SCAN)
    except Exception as exc:
        logger.warning("审计 Redis 查询失败，回落 Postgres 持久层：%s", exc)
        return []
    records = [parse_entry(entry_id, fields) for entry_id, fields in entries]
    return _apply_filters(records, action=action, actor=actor)


async def _read_postgres(
    session: AsyncSession,
    *,
    action: str | None,
    actor: str | None,
    page: int,
    page_size: int,
) -> AuditListResponse:
    """读 Postgres 持久层。两层都不可用时返回空集并把 source 标成 none。"""
    try:
        entries, total = await PostgresAuditLogStore(session).list(
            action=action, actor=actor, limit=page_size, offset=(page - 1) * page_size
        )
    except Exception as exc:
        logger.error("审计 Redis 与 Postgres 均不可用，本次查询返回空集：%s", exc)
        return AuditListResponse(
            items=[], total=0, page=page, page_size=page_size, source="none"
        )
    return AuditListResponse(
        items=[AuditRecord(**e.to_dict()) for e in entries],
        total=total,
        page=page,
        page_size=page_size,
        source="postgres",
    )


def _apply_filters(
    records: list[dict[str, Any]],
    *,
    action: str | None,
    actor: str | None,
) -> list[dict[str, Any]]:
    """按 action（精确）与 actor（模糊、忽略大小写）过滤。"""
    if action:
        records = [r for r in records if r["action"] == action]
    if actor:
        needle = actor.lower()
        records = [r for r in records if needle in r["actor"].lower()]
    return records
