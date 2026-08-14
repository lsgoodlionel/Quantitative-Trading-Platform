"""
自动因子循环 —— 轮次记录存储（V3 · I2）

一轮循环走 Celery 异步执行，端点立刻返回 `round_id`，前端轮询取结果。
中间这份「状态 + 结果」落 Redis，布局仿 `app/quant/experiments/recorder.py`::

    lab:auto_loop:round:{round_id}  → 记录 JSON（SET）
    lab:auto_loop:rounds            → ZSET member=round_id score=created_at

⚠️ 容量：`MAX_ROUNDS` 条滚动淘汰。**这是刻意与产物库不同的选择**：
轮次记录只是过程状态（进度、复盘文字、指标快照），真正要长期留存的因子
已经作为产物写进了 `LabStore`（无上限、仅手动删除）。轮次记录被淘汰后，
它引用的 `artifact_id` 照样加载得到 —— 这正是产物库存在的理由。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "lab:auto_loop"
_RECORD_KEY = f"{_KEY_PREFIX}:round"
_ZSET_TIME = f"{_KEY_PREFIX}:rounds"

#: 轮次记录容量上限（见模块 docstring 的取舍说明）
MAX_ROUNDS = 100

#: 合法状态。queued → running → done / error
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class RoundRecord:
    """一轮循环的状态与结果。"""

    round_id: str
    status: str
    created_at: float
    updated_at: float
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "round_id": self.round_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request": dict(self.request),
            "result": self.result,
            "error": self.error,
        }


def new_record(round_id: str, request: dict[str, Any]) -> RoundRecord:
    now = time.time()
    return RoundRecord(
        round_id=round_id,
        status=STATUS_QUEUED,
        created_at=now,
        updated_at=now,
        request=dict(request),
    )


async def save_round(redis: aioredis.Redis, record: RoundRecord) -> RoundRecord:
    """写入/覆盖一条轮次记录并登记到时间线。"""
    stamped = replace(record, updated_at=time.time())
    payload = json.dumps(stamped.to_dict(), ensure_ascii=False, default=str)
    pipe = redis.pipeline()
    pipe.set(_record_key(stamped.round_id), payload)
    pipe.zadd(_ZSET_TIME, {stamped.round_id: stamped.created_at})
    await pipe.execute()
    await _enforce_capacity(redis)
    return stamped


async def mark_status(
    redis: aioredis.Redis,
    round_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> RoundRecord | None:
    """把一轮记录推进到新状态。记录不存在（已被淘汰）时返回 None，不报错。"""
    record = await get_round(redis, round_id)
    if record is None:
        logger.warning("自动因子循环轮次记录已不存在，跳过状态更新: %s", round_id)
        return None
    return await save_round(
        redis, replace(record, status=status, result=result, error=error)
    )


async def get_round(redis: aioredis.Redis, round_id: str) -> RoundRecord | None:
    raw = await redis.get(_record_key(round_id))
    return _parse(raw) if raw else None


async def list_rounds(redis: aioredis.Redis, limit: int = 20) -> list[RoundRecord]:
    """按创建时间倒序列出轮次记录。"""
    ids = await redis.zrevrange(_ZSET_TIME, 0, max(limit, 1) - 1)
    if not ids:
        return []
    raws = await redis.mget([_record_key(rid) for rid in ids])
    records = [_parse(raw) for raw in raws if raw]
    return [r for r in records if r is not None]


async def delete_round(redis: aioredis.Redis, round_id: str) -> bool:
    existed = await redis.exists(_record_key(round_id))
    pipe = redis.pipeline()
    pipe.delete(_record_key(round_id))
    pipe.zrem(_ZSET_TIME, round_id)
    await pipe.execute()
    return bool(existed)


# ── 内部 ──────────────────────────────────────────────────────────

def _record_key(round_id: str) -> str:
    return f"{_RECORD_KEY}:{round_id}"


async def _enforce_capacity(redis: aioredis.Redis) -> None:
    total = await redis.zcard(_ZSET_TIME)
    overflow = int(total) - MAX_ROUNDS
    if overflow <= 0:
        return
    stale = await redis.zrange(_ZSET_TIME, 0, overflow - 1)
    if not stale:
        return
    pipe = redis.pipeline()
    for rid in stale:
        pipe.delete(_record_key(rid))
        pipe.zrem(_ZSET_TIME, rid)
    await pipe.execute()


def _parse(raw: str) -> RoundRecord | None:
    try:
        data = json.loads(raw)
        return RoundRecord(
            round_id=data["round_id"],
            status=data["status"],
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            request=data.get("request", {}),
            result=data.get("result"),
            error=data.get("error"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning("自动因子循环轮次记录解析失败，已跳过")
        return None
