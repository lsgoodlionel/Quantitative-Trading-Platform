"""
再训练 / 漂移检测 —— 作业记录存储（V4 · M6）

一次重训走 Celery 异步执行，端点立刻返回 `job_id`，前端轮询取结果。
中间这份「状态 + 结果」落 Redis，布局与 `app/quant/lab/loop_store.py` 一致::

    quant:retrain:job:{job_id}  → 记录 JSON（SET）
    quant:retrain:jobs          → ZSET member=job_id score=created_at

为什么不复用 `loop_store` 而是另起一份：那边的键前缀、容量上限与
`RoundRecord` 字段都是 I2 的形状（`round_id` / 复盘文字）。把它参数化会动到
一条已经在跑的链路，收益只有省下这一百行 —— 不划算。两边任一侧要改结构时，
另一侧不该被牵连。

⚠️ 容量：`MAX_JOBS` 条滚动淘汰。**过程记录会被淘汰，产物不会** ——
重训出来的模型已作为 MODEL 产物写进 `LabStore`（无上限、仅手动删除），
作业记录被淘汰后 `artifact_id` 照样加载得到。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "quant:retrain"
_RECORD_KEY = f"{_KEY_PREFIX}:job"
_ZSET_TIME = f"{_KEY_PREFIX}:jobs"

#: 作业记录容量上限（见模块 docstring 的取舍说明）
MAX_JOBS = 100

#: 合法状态。queued → running → done / error
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"

#: 作业类型
KIND_RETRAIN = "retrain"
KIND_DRIFT_CHECK = "drift_check"


def new_job_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class RetrainJobRecord:
    """一次重训 / 漂移检测的状态与结果。"""

    job_id: str
    kind: str
    status: str
    created_at: float
    updated_at: float
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request": dict(self.request),
            "result": self.result,
            "error": self.error,
        }


def new_record(job_id: str, kind: str, request: dict[str, Any]) -> RetrainJobRecord:
    now = time.time()
    return RetrainJobRecord(
        job_id=job_id,
        kind=kind,
        status=STATUS_QUEUED,
        created_at=now,
        updated_at=now,
        request=dict(request),
    )


async def save_job(redis: aioredis.Redis, record: RetrainJobRecord) -> RetrainJobRecord:
    """写入/覆盖一条作业记录并登记到时间线。"""
    stamped = replace(record, updated_at=time.time())
    payload = json.dumps(stamped.to_dict(), ensure_ascii=False, default=str)
    pipe = redis.pipeline()
    pipe.set(_record_key(stamped.job_id), payload)
    pipe.zadd(_ZSET_TIME, {stamped.job_id: stamped.created_at})
    await pipe.execute()
    await _enforce_capacity(redis)
    return stamped


async def mark_status(
    redis: aioredis.Redis,
    job_id: str,
    status: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> RetrainJobRecord | None:
    """推进到新状态。记录不存在（已被淘汰）时返回 None，不报错。"""
    record = await get_job(redis, job_id)
    if record is None:
        logger.warning("再训练作业记录已不存在，跳过状态更新: %s", job_id)
        return None
    return await save_job(redis, replace(record, status=status, result=result, error=error))


async def get_job(redis: aioredis.Redis, job_id: str) -> RetrainJobRecord | None:
    raw = await redis.get(_record_key(job_id))
    return _parse(raw) if raw else None


async def list_jobs(redis: aioredis.Redis, limit: int = 20) -> list[RetrainJobRecord]:
    """按创建时间倒序列出作业记录。"""
    ids = await redis.zrevrange(_ZSET_TIME, 0, max(limit, 1) - 1)
    if not ids:
        return []
    raws = await redis.mget([_record_key(jid) for jid in ids])
    records = [_parse(raw) for raw in raws if raw]
    return [r for r in records if r is not None]


async def delete_job(redis: aioredis.Redis, job_id: str) -> bool:
    existed = await redis.exists(_record_key(job_id))
    pipe = redis.pipeline()
    pipe.delete(_record_key(job_id))
    pipe.zrem(_ZSET_TIME, job_id)
    await pipe.execute()
    return bool(existed)


# ── 内部 ──────────────────────────────────────────────────────────

def _record_key(job_id: str) -> str:
    return f"{_RECORD_KEY}:{job_id}"


async def _enforce_capacity(redis: aioredis.Redis) -> None:
    total = await redis.zcard(_ZSET_TIME)
    overflow = int(total) - MAX_JOBS
    if overflow <= 0:
        return
    stale = await redis.zrange(_ZSET_TIME, 0, overflow - 1)
    if not stale:
        return
    pipe = redis.pipeline()
    for jid in stale:
        pipe.delete(_record_key(jid))
        pipe.zrem(_ZSET_TIME, jid)
    await pipe.execute()


def _parse(raw: str) -> RetrainJobRecord | None:
    try:
        data = json.loads(raw)
        return RetrainJobRecord(
            job_id=data["job_id"],
            kind=data.get("kind", KIND_RETRAIN),
            status=data["status"],
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            request=data.get("request", {}),
            result=data.get("result"),
            error=data.get("error"),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        logger.warning("再训练作业记录解析失败，已跳过")
        return None
