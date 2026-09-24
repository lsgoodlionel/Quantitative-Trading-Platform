"""
自适应再训练 / 漂移检测 API（V4 · M6）

  POST   /retrain/jobs              手动触发一次重训（异步，立即返回 job_id）
  POST   /retrain/drift-checks      手动触发一次漂移检测（异步）
  GET    /retrain/jobs              作业历史（按时间倒序，重训与检测混排）
  GET    /retrain/jobs/{job_id}     单次作业的状态与结果
  DELETE /retrain/jobs/{job_id}     删除一条作业记录
  GET    /retrain/schedule          定时调度的当前开关与参数（只读）

⚠️ **这里没有「上线 / 替换当前模型」的端点，也不会有。**
重训只把新模型写进产物库（`LabStore`，`kind=model`，标签 `activated=false`），
替换线上模型走人工路径。与自动因子循环（I2）同一立场：
一个没人看过的自动产物直接进入交易链路，是这类系统最典型的事故来源。

⚠️ **漂移检测端点不会触发重训。** 结果里的 `retrain_triggered` 恒为 False。

⚠️ 入参校验在**提交那一刻**同步做完（标的非空、窗口长度、模型类型、日期格式），
让用户立刻知道配置错了，而不是轮询二十分钟拿到一个「模型类型非法」。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.redis import get_redis
from app.quant.retrain import (
    DEFAULT_HOLDOUT_RATIO,
    DEFAULT_LOOKBACK_DAYS,
    MODEL_KINDS,
    RetrainConfig,
    RetrainError,
)
from app.quant.retrain_store import (
    KIND_DRIFT_CHECK,
    KIND_RETRAIN,
    STATUS_QUEUED,
    RetrainJobRecord,
    delete_job,
    get_job,
    list_jobs,
    new_job_id,
    new_record,
    save_job,
)

router = APIRouter()

#: 一次重训最多接受多少个标的
MAX_SYMBOLS = 50
#: 列表端点的页大小上限
MAX_LIST_LIMIT = 100

RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]

ACTIVATION_NOTE = (
    "重训产出只写入产物库，**不会**自动替换线上模型；确认指标后请人工上线。"
)
DRIFT_NOTE = (
    "漂移检测只发通知，**不会**自动触发重训 —— 市场剧变当天重训，学到的正是那天的噪声。"
)


class RetrainRequest(BaseModel):
    """一次再训练的配置。"""

    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS, description="标的列表")
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    lookback_days: int = Field(default=DEFAULT_LOOKBACK_DAYS, ge=30, le=3650)
    end: str | None = Field(default=None, description="滚动窗口右端（YYYY-MM-DD），缺省为今天")
    forward_period: int = Field(default=5, ge=1, le=60)
    holdout_ratio: float = Field(default=DEFAULT_HOLDOUT_RATIO, ge=0.05, le=0.5)
    model_kind: Literal["lasso", "gradient_boosting"] = "lasso"
    previous_artifact_id: str | None = Field(
        default=None, description="上一版模型产物 ID —— 给出后新旧模型会在同一段样本外并排对比"
    )


class DriftCheckRequest(BaseModel):
    """一次漂移检测的配置。特征取自 `artifact_id` 对应模型训练时的那一套。"""

    artifact_id: str = Field(min_length=1, description="带训练集分布快照的模型产物 ID")
    symbols: list[str] = Field(min_length=1, max_length=MAX_SYMBOLS)
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    lookback_days: int = Field(default=180, ge=30, le=3650, description="检测窗口长度（自然日）")
    end: str | None = None
    forward_period: int = Field(default=5, ge=1, le=60)
    di_threshold: float | None = Field(default=None, gt=0, description="单样本判离群的 DI 阈值")
    outlier_ratio_threshold: float | None = Field(
        default=None, gt=0, le=1, description="判 is_drifting 的离群占比阈值"
    )


class JobSubmitResponse(BaseModel):
    job_id: str
    kind: str
    status: str = STATUS_QUEUED
    note: str


class JobResponse(BaseModel):
    job_id: str
    kind: str
    #: queued / running / done / error
    status: str
    created_at: float
    updated_at: float
    request: dict
    result: dict | None = None
    error: str | None = None


class JobListResponse(BaseModel):
    total: int
    items: list[JobResponse]


class ScheduleResponse(BaseModel):
    enabled: bool
    symbols: list[str]
    market: str
    model_kind: str
    day_of_week: int
    hour: int
    note: str


# ── 提交 ──────────────────────────────────────────────────────────

@router.post("/jobs", response_model=JobSubmitResponse)
async def start_retrain(body: RetrainRequest, redis: RedisDep) -> JobSubmitResponse:
    """手动触发一次再训练。立即返回 `job_id`，结果轮询 GET 取。"""
    payload = _retrain_payload(body)
    job_id = await _submit(redis, KIND_RETRAIN, payload, "run_retrain_task")
    return JobSubmitResponse(job_id=job_id, kind=KIND_RETRAIN, note=ACTIVATION_NOTE)


@router.post("/drift-checks", response_model=JobSubmitResponse)
async def start_drift_check(body: DriftCheckRequest, redis: RedisDep) -> JobSubmitResponse:
    """手动触发一次漂移检测。⚠️ 只检测、只通知，不重训。"""
    payload = _drift_payload(body)
    job_id = await _submit(redis, KIND_DRIFT_CHECK, payload, "run_drift_check_task")
    return JobSubmitResponse(job_id=job_id, kind=KIND_DRIFT_CHECK, note=DRIFT_NOTE)


# ── 查询 / 删除 ───────────────────────────────────────────────────

@router.get("/jobs", response_model=JobListResponse)
async def get_retrain_jobs(
    redis: RedisDep,
    limit: int = Query(20, ge=1, le=MAX_LIST_LIMIT),
) -> JobListResponse:
    """按时间倒序列出作业记录（重训与漂移检测混排，用 `kind` 区分）。"""
    records = await list_jobs(redis, limit=limit)
    return JobListResponse(total=len(records), items=[_to_response(r) for r in records])


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_retrain_job(job_id: str, redis: RedisDep) -> JobResponse:
    """查询单次作业的状态与结果。"""
    record = await get_job(redis, job_id)
    if record is None:
        raise HTTPException(404, f"作业记录不存在: {job_id}")
    return _to_response(record)


@router.delete("/jobs/{job_id}")
async def remove_retrain_job(job_id: str, redis: RedisDep) -> dict:
    """删除一条作业记录。⚠️ 只删过程记录，已入库的模型产物不受影响。"""
    removed = await delete_job(redis, job_id)
    if not removed:
        raise HTTPException(404, f"作业记录不存在: {job_id}")
    return {"job_id": job_id, "deleted": True}


@router.get("/schedule", response_model=ScheduleResponse)
async def get_retrain_schedule() -> ScheduleResponse:
    """
    定时调度的当前状态（只读）。

    刻意**不提供 PUT**：开关走部署配置（环境变量），不走 API。
    一个能被 HTTP 调用打开的无人值守训练任务，出问题时没人说得清是谁开的。
    """
    symbols = [s.strip().upper() for s in settings.retrain_symbols.split(",") if s.strip()]
    return ScheduleResponse(
        enabled=settings.retrain_schedule_enabled,
        symbols=symbols,
        market=settings.retrain_market,
        model_kind=settings.retrain_model_kind,
        day_of_week=settings.retrain_schedule_day_of_week,
        hour=settings.retrain_schedule_hour,
        note=(
            "开关只能通过部署配置（retrain_schedule_enabled）修改，API 不提供写入。"
            + ACTIVATION_NOTE
        ),
    )


# ── 内部 ──────────────────────────────────────────────────────────

def _retrain_payload(body: RetrainRequest) -> dict:
    payload = body.model_dump(mode="json")
    _validate_config(payload)
    return payload


def _drift_payload(body: DriftCheckRequest) -> dict:
    payload = body.model_dump(mode="json")
    # 漂移检测不训练，但复用同一个 RetrainConfig 做窗口/取数校验
    _validate_config({**payload, "model_kind": "lasso", "holdout_ratio": DEFAULT_HOLDOUT_RATIO})
    return payload


def _validate_config(payload: dict) -> None:
    """提交时同步跑一遍 `RetrainConfig` 的校验，非法配置当场 400。"""
    end_raw = payload.get("end")
    try:
        end = date.fromisoformat(str(end_raw)) if end_raw else None
    except ValueError as exc:
        raise HTTPException(400, f"end 非法（需 YYYY-MM-DD）: {end_raw}") from exc

    symbols = tuple(str(s).strip().upper() for s in payload.get("symbols", []) if str(s).strip())
    if not symbols:
        raise HTTPException(400, "symbols 去空后为空")
    if payload.get("model_kind") not in MODEL_KINDS:
        raise HTTPException(400, f"model_kind 需为 {list(MODEL_KINDS)} 之一")

    try:
        RetrainConfig(
            symbols=symbols,
            market=str(payload.get("market", "US")),
            frequency=str(payload.get("frequency", "1d")),
            lookback_days=int(payload.get("lookback_days") or DEFAULT_LOOKBACK_DAYS),
            end=end,
            forward_period=int(payload.get("forward_period") or 5),
            holdout_ratio=float(payload.get("holdout_ratio") or DEFAULT_HOLDOUT_RATIO),
            model_kind=str(payload.get("model_kind", "lasso")),
        )
    except RetrainError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _submit(redis, kind: str, payload: dict, task_name: str) -> str:
    """落一条 queued 记录 → 派发 Celery。派发失败就把记录删掉再报错。"""
    job_id = new_job_id()
    payload["job_id"] = job_id
    await save_job(redis, new_record(job_id, kind, payload))

    import app.tasks.retrain as task_module

    try:
        getattr(task_module, task_name).delay(payload)
    except Exception as exc:  # broker 不可用
        # 留下一条 queued 却永远不会被执行的记录是最坏的结果 —— 直接删掉再报错。
        await delete_job(redis, job_id)
        raise HTTPException(503, f"任务队列不可用: {exc}") from exc
    return job_id


def _to_response(record: RetrainJobRecord) -> JobResponse:
    return JobResponse(**record.to_dict())
