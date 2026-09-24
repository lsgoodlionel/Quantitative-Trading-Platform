"""
自适应再训练 / 漂移检测的 Celery 任务（V4 · M6）

与 `app/tasks/auto_loop.py` 同一约定：Celery worker 是同步进程，
异步取数与入库经 `asyncio.run()` 桥接，且**自建 engine/session**
（worker 里没有 FastAPI 的 request context，拿不到 `Depends(get_db)` 那套）。

⚠️ **worker 进程拿不到 FastAPI 进程内的任何单例**（Wave C-b 的坑：
`get_order_manager()` 是模块级全局，worker 里永远是空的）。
本模块只依赖数据库 / 文件系统 / Redis，不碰任何进程内状态。

三个任务
------------------------------------------------------------------
- `run_retrain_task`        手动触发一次重训（端点派发）
- `run_drift_check_task`    手动触发一次漂移检测（端点派发）
- `scheduled_retrain_task`  beat 周期任务，**默认关闭**，由
  `settings.retrain_schedule_enabled` 显式开启

⚠️ 定时任务与手动任务对开关的态度**刻意不同**：`scheduled_retrain_task`
关闭时直接跳过（无人值守的东西默认不跑），手动任务不看开关（人点的那一下
本身就是授权）。

⚠️ 漂移检测**不会**在这里 chain 一个重训任务，将来也不要加。
市场剧变当天自动重训，学到的正是那天的噪声。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)

#: 软/硬超时。一次重训 = 取数 + 训练 + 两次样本外打分，比回测短但比默认 5 分钟长。
_SOFT_TIME_LIMIT = 1200   # 20 分钟
_HARD_TIME_LIMIT = 1500   # 25 分钟


@shared_task(
    name="app.tasks.retrain.run_retrain_task",
    bind=True,
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_HARD_TIME_LIMIT,
    # 失败原因（数据不足、窗口太短、模型类型非法）重试也不会变好，
    # 而重试一次是又一轮完整训练的算力。
    max_retries=0,
)
def run_retrain_task(self, payload: dict) -> dict:
    """异步跑一次再训练。`payload` 必须含 `job_id`。"""
    return asyncio.run(_run_retrain_guarded(payload))


@shared_task(
    name="app.tasks.retrain.run_drift_check_task",
    bind=True,
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_HARD_TIME_LIMIT,
    max_retries=0,
)
def run_drift_check_task(self, payload: dict) -> dict:
    """异步跑一次漂移检测。`payload` 必须含 `job_id` 与 `artifact_id`。"""
    return asyncio.run(_run_drift_check_guarded(payload))


@shared_task(name="app.tasks.retrain.scheduled_retrain_task", bind=True, max_retries=0)
def scheduled_retrain_task(self) -> dict:
    """
    beat 周期入口。**默认关闭** —— 未显式开启时什么也不做。

    返回 `{"status": "disabled"}` 而不是静默 return：beat 每周唤醒一次却
    什么都没发生，日志里得留下「是因为开关关着」的证据。
    """
    from app.core.config import settings

    if not settings.retrain_schedule_enabled:
        logger.info("定时再训练未启用（retrain_schedule_enabled=False），跳过本次调度")
        return {"status": "disabled", "reason": "settings.retrain_schedule_enabled 为 False"}

    symbols = _scheduled_symbols(settings)
    if not symbols:
        logger.warning("定时再训练已启用但 retrain_symbols 为空，跳过")
        return {"status": "skipped", "reason": "settings.retrain_symbols 为空"}

    payload = {
        "job_id": _new_job_id(),
        "symbols": symbols,
        "market": settings.retrain_market,
        "model_kind": settings.retrain_model_kind,
        "source": "beat",
    }
    return asyncio.run(_run_retrain_guarded(payload, register=True))


# ── 内部：状态机包装 ──────────────────────────────────────────────

async def _run_retrain_guarded(payload: dict, register: bool = False) -> dict:
    """
    全程包在作业记录的状态机里：running → done / error，绝不留下悬空的 queued。

    失败时**必发通知**：一个每周静默失败的定时重训，表现是「模型三个月没更新过，
    而没有任何人知道」。
    """
    from app.notify.emit import emit_retrain_done, emit_retrain_failed
    from app.quant.retrain import run_retrain
    from app.quant.retrain_store import (
        KIND_RETRAIN,
        STATUS_DONE,
        STATUS_ERROR,
        STATUS_RUNNING,
        mark_status,
        new_record,
        save_job,
    )

    job_id = str(payload.get("job_id", ""))
    model_kind = str(payload.get("model_kind", "lasso"))
    market = str(payload.get("market", "US"))

    async with _redis() as redis:
        try:
            if register:
                await save_job(redis, new_record(job_id, KIND_RETRAIN, payload))
            await mark_status(redis, job_id, STATUS_RUNNING)
            config = _build_config(payload)
            outcome = await run_retrain(config)
            result = outcome.to_dict()
            await mark_status(redis, job_id, STATUS_DONE, result=result)
            emit_retrain_done(
                model_kind=config.model_kind,
                market=config.market,
                artifact_id=outcome.artifact_id,
                new_metrics=outcome.new_metrics,
                previous_metrics=outcome.previous_metrics,
                window=f"{outcome.window_start} ~ {outcome.window_end}",
            )
            return {"status": "ok", "job_id": job_id, "result": result}
        except Exception as exc:  # noqa: BLE001 — 原因必须回到前端
            logger.exception("再训练失败: job_id=%s", job_id)
            await _record_failure(redis, job_id, str(exc), mark_status, STATUS_ERROR)
            emit_retrain_failed(model_kind=model_kind, market=market, reason=str(exc))
            return {"status": "error", "job_id": job_id, "error": str(exc)}


async def _run_drift_check_guarded(payload: dict) -> dict:
    """
    漂移检测的状态机包装。

    ⚠️ 检测到漂移只 `emit_model_drift`，**不派发任何重训任务**。
    这里不存在通往 `run_retrain_task` 的代码路径，也不要加一条。
    """
    from app.notify.emit import emit_model_drift
    from app.quant.retrain import run_drift_check
    from app.quant.retrain_store import (
        STATUS_DONE,
        STATUS_ERROR,
        STATUS_RUNNING,
        mark_status,
    )

    job_id = str(payload.get("job_id", ""))
    artifact_id = str(payload.get("artifact_id", ""))

    async with _redis() as redis:
        try:
            await mark_status(redis, job_id, STATUS_RUNNING)
            config = _build_config(payload)
            result = await run_drift_check(
                artifact_id,
                config,
                di_threshold=float(payload.get("di_threshold") or _default_di()),
                outlier_ratio_threshold=float(
                    payload.get("outlier_ratio_threshold") or _default_ratio()
                ),
            )
            await mark_status(redis, job_id, STATUS_DONE, result=result)
            _notify_if_drifting(emit_model_drift, artifact_id, config.market, result)
            return {"status": "ok", "job_id": job_id, "result": result}
        except Exception as exc:  # noqa: BLE001 — 原因必须回到前端
            logger.exception("漂移检测失败: job_id=%s", job_id)
            await _record_failure(redis, job_id, str(exc), mark_status, STATUS_ERROR)
            return {"status": "error", "job_id": job_id, "error": str(exc)}


def _notify_if_drifting(emit, artifact_id: str, market: str, result: dict) -> None:
    """
    只在判定漂移时发通知。

    每次检测都响一下，等于训练用户把这个通知当噪音忽略掉，真漂了也就没人看了
    （与 `emit_data_gap` / `emit_reconcile_diff` 同一取舍）。
    """
    report = result.get("report") or {}
    if not report.get("is_drifting"):
        return
    emit(
        artifact_id=artifact_id,
        market=market,
        outlier_ratio=float(report.get("outlier_ratio", 0.0)),
        threshold=float(report.get("threshold", 0.0)),
        outlier_ratio_threshold=float(report.get("outlier_ratio_threshold", 0.0)),
        n_samples=int(report.get("n_samples", 0)),
        sampling_note=str(report.get("sampling_note", "")),
    )


def _build_config(payload: dict) -> Any:
    """payload → `RetrainConfig`。非法入参在这里抛，由上层记进作业记录。"""
    from datetime import date

    from app.quant.retrain import (
        DEFAULT_HOLDOUT_RATIO,
        DEFAULT_LOOKBACK_DAYS,
        RetrainConfig,
    )

    end_raw = payload.get("end")
    return RetrainConfig(
        symbols=tuple(str(s).strip().upper() for s in payload.get("symbols", []) if str(s).strip()),
        market=str(payload.get("market", "US")),
        frequency=str(payload.get("frequency", "1d")),
        lookback_days=int(payload.get("lookback_days") or DEFAULT_LOOKBACK_DAYS),
        end=date.fromisoformat(str(end_raw)) if end_raw else None,
        forward_period=int(payload.get("forward_period") or 5),
        holdout_ratio=float(payload.get("holdout_ratio") or DEFAULT_HOLDOUT_RATIO),
        model_kind=str(payload.get("model_kind", "lasso")),
        previous_artifact_id=payload.get("previous_artifact_id") or None,
    )


def _default_di() -> float:
    from app.quant.drift import DEFAULT_DI_THRESHOLD

    return DEFAULT_DI_THRESHOLD


def _default_ratio() -> float:
    from app.quant.drift import DEFAULT_OUTLIER_RATIO_THRESHOLD

    return DEFAULT_OUTLIER_RATIO_THRESHOLD


def _new_job_id() -> str:
    from app.quant.retrain_store import new_job_id

    return new_job_id()


def _scheduled_symbols(settings: Any) -> list[str]:
    """定时重训的标的清单来自配置的逗号分隔串。"""
    return [s.strip().upper() for s in str(settings.retrain_symbols).split(",") if s.strip()]


async def _record_failure(redis, job_id: str, message: str, mark, status: str) -> None:
    """把失败写进作业记录。**写记录本身再失败也不能吞掉原始错误** —— 那才是要看的。"""
    try:
        await mark(redis, job_id, status, error=message)
    except Exception:  # noqa: BLE001
        logger.exception("写入再训练失败状态时再次出错: job_id=%s", job_id)


@asynccontextmanager
async def _redis() -> AsyncIterator[Any]:
    """worker 进程内自建 Redis 连接（与 `app/tasks/auto_loop.py` 同一约定）。"""
    import redis.asyncio as aioredis

    from app.core.redis import get_redis_pool

    client = aioredis.Redis(connection_pool=get_redis_pool())
    try:
        yield client
    finally:
        await client.aclose()
