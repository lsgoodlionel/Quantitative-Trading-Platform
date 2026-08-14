"""
自动因子研发循环 API（V3 · I2）

  POST   /lab/auto-loop              启动一轮（异步，立即返回 round_id）
  GET    /lab/auto-loop              轮次列表（按时间倒序）
  GET    /lab/auto-loop/{round_id}   单轮状态与结果
  DELETE /lab/auto-loop/{round_id}   删除一条轮次记录

⚠️ **这里没有「上线」端点，也不会有。** 循环只把因子写进产物库
（`LabStore`），晋级为策略走已有的人工路径
（`app/quant/experiments/recorder.py::promote_to_strategy`）。
一个没人看过的自动挖掘结果直接进入交易链路，是这类系统最典型的事故来源。

⚠️ **入参校验在提交那一刻同步做完**（universe 规模、is_end 格式、资源上限），
让用户立刻知道配置错了，而不是轮询几分钟拿到一个「universe 太小」。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.redis import get_redis
from app.quant.lab.auto_loop import DEFAULT_SEED_EXPRESSIONS, new_round_id
from app.quant.lab.loop_store import (
    STATUS_QUEUED,
    RoundRecord,
    delete_round,
    get_round,
    list_rounds,
    new_record,
    save_round,
)

router = APIRouter()

#: 一次请求最多接受多少条种子表达式
MAX_SEEDS = 20
#: 列表端点的页大小上限
MAX_LIST_LIMIT = 100

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]


class AutoLoopRequest(BaseModel):
    """一轮自动循环的配置。"""

    universe: list[str] = Field(min_length=3, max_length=40, description="标的列表")
    is_end: date = Field(description="样本内截止日（含当日）；其后的数据在搜索期不可见")
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    start: str | None = Field(default=None, description="取数起始日，缺省为 end 前两年")
    end: str | None = Field(default=None, description="取数截止日，缺省为今天")
    forward_period: int = Field(default=5, ge=1, le=60)
    generations: int = Field(default=5, ge=1, le=30)
    population: int = Field(default=40, ge=6, le=120)
    top_k: int = Field(default=5, ge=1, le=30)
    max_candidates_evaluated: int = Field(default=2000, ge=10, le=20_000)
    max_depth: int = Field(default=4, ge=2, le=6)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    use_cross_section: bool = False
    seeds: list[str] = Field(
        default_factory=lambda: list(DEFAULT_SEED_EXPRESSIONS),
        max_length=MAX_SEEDS,
        description="初代种群的种子表达式；非法的会被丢弃并计数，不会让本轮失败",
    )


class AutoLoopSubmitResponse(BaseModel):
    round_id: str
    status: str = STATUS_QUEUED


class AutoLoopRoundResponse(BaseModel):
    round_id: str
    #: queued / running / done / error
    status: str
    created_at: float
    updated_at: float
    request: dict
    result: dict | None = None
    error: str | None = None


class AutoLoopListResponse(BaseModel):
    total: int
    items: list[AutoLoopRoundResponse]


@router.post("/auto-loop", response_model=AutoLoopSubmitResponse)
async def start_auto_loop(body: AutoLoopRequest, redis: RedisDep) -> AutoLoopSubmitResponse:
    """启动一轮自动因子研发循环。立即返回 `round_id`，结果轮询 GET 取。"""
    _validate_dates(body)

    round_id = new_round_id()
    payload = body.model_dump(mode="json")
    payload["round_id"] = round_id
    await save_round(redis, new_record(round_id, payload))

    from app.tasks.auto_loop import run_auto_factor_loop_task

    try:
        run_auto_factor_loop_task.delay(payload)
    except Exception as exc:  # broker 不可用
        # 留下一条 queued 却永远不会被执行的记录是最坏的结果 —— 直接删掉再报错。
        await delete_round(redis, round_id)
        raise HTTPException(503, f"任务队列不可用: {exc}") from exc

    return AutoLoopSubmitResponse(round_id=round_id)


@router.get("/auto-loop", response_model=AutoLoopListResponse)
async def get_auto_loop_rounds(
    redis: RedisDep,
    limit: int = Query(20, ge=1, le=MAX_LIST_LIMIT),
) -> AutoLoopListResponse:
    """按时间倒序列出轮次记录。"""
    records = await list_rounds(redis, limit=limit)
    return AutoLoopListResponse(
        total=len(records), items=[_to_response(r) for r in records]
    )


@router.get("/auto-loop/{round_id}", response_model=AutoLoopRoundResponse)
async def get_auto_loop_round(round_id: str, redis: RedisDep) -> AutoLoopRoundResponse:
    """查询单轮状态与结果。"""
    record = await get_round(redis, round_id)
    if record is None:
        raise HTTPException(404, f"轮次记录不存在: {round_id}")
    return _to_response(record)


@router.delete("/auto-loop/{round_id}")
async def remove_auto_loop_round(round_id: str, redis: RedisDep) -> dict:
    """删除一条轮次记录。⚠️ 只删过程记录，已入库的产物不受影响。"""
    removed = await delete_round(redis, round_id)
    if not removed:
        raise HTTPException(404, f"轮次记录不存在: {round_id}")
    return {"round_id": round_id, "deleted": True}


# ── 内部 ──────────────────────────────────────────────────────────

def _validate_dates(body: AutoLoopRequest) -> None:
    """`is_end` 必须落在取数区间内，否则样本内或样本外必有一边是空的。"""
    if body.end:
        try:
            end_date = date.fromisoformat(body.end)
        except ValueError as exc:
            raise HTTPException(400, f"end 非法（需 YYYY-MM-DD）: {body.end}") from exc
        if body.is_end >= end_date:
            raise HTTPException(
                400,
                f"is_end（{body.is_end}）必须早于取数截止日（{body.end}），"
                "否则样本外没有任何数据 —— 那样搜出来的 IC 只是搜索强度的度量。",
            )
    if body.start:
        try:
            start_date = date.fromisoformat(body.start)
        except ValueError as exc:
            raise HTTPException(400, f"start 非法（需 YYYY-MM-DD）: {body.start}") from exc
        if body.is_end <= start_date:
            raise HTTPException(
                400, f"is_end（{body.is_end}）必须晚于取数起始日（{body.start}）"
            )


def _to_response(record: RoundRecord) -> AutoLoopRoundResponse:
    return AutoLoopRoundResponse(**record.to_dict())
