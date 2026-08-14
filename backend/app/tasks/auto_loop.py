"""自动因子研发循环的 Celery 任务（V3 · I2）

一轮循环 = 遗传搜索几百到几千次公式求值 + 一次 LLM 复盘，以分钟计。
同步端点在这个量级上必然撞网关超时，所以只提供异步入口：
提交拿 `round_id`，轮询取结果。

与 `app/tasks/validation.py` 同一约定：Celery worker 是同步进程，
异步取数经 `asyncio.run()` 桥接，且**自建 engine/session**
（worker 里没有 FastAPI 的 request context，拿不到 `Depends(get_db)` 那套）。

⚠️ 刻意**不挂 beat 定时调度**。契约 §4：不做无人值守的定时循环 ——
手动触发一轮跑通再说。一个每晚自己跑、没人看结果的挖掘任务，
产出的只是一堆无人复核的过拟合公式加一笔算力账单。
"""

from __future__ import annotations

import asyncio
import logging

from celery import shared_task

logger = logging.getLogger(__name__)

#: 软/硬超时。搜索规模由用户配置（代数 × 种群 × universe），
#: 用全局默认（5/10 分钟）会在大参数下被 worker 杀掉。
_SOFT_TIME_LIMIT = 1800   # 30 分钟
_HARD_TIME_LIMIT = 2100   # 35 分钟


@shared_task(
    name="app.tasks.auto_loop.run_auto_factor_loop_task",
    bind=True,
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_HARD_TIME_LIMIT,
    # 纯计算 + 一次模型调用，重试只会把同样的输入再算一遍；
    # 真正的失败原因（数据不足、is_end 非法）重试也不会变好。
    max_retries=0,
)
def run_auto_factor_loop_task(self, payload: dict) -> dict:
    """异步跑一轮自动因子循环。

    Args:
        payload: 端点组装的请求体，必须含 `round_id`

    Returns:
        `{"status": "ok"|"error", "round_id": ..., "result"|"error": ...}`
    """
    return asyncio.run(_run_guarded(payload))


async def _run_guarded(payload: dict) -> dict:
    """全程包在轮次记录的状态机里：running → done / error，绝不留下悬空的 queued。"""
    import redis.asyncio as aioredis

    from app.core.redis import get_redis_pool
    from app.quant.lab.loop_runner import execute_round
    from app.quant.lab.loop_store import (
        STATUS_DONE,
        STATUS_ERROR,
        STATUS_RUNNING,
        mark_status,
    )

    round_id = str(payload.get("round_id", ""))
    redis = aioredis.Redis(connection_pool=get_redis_pool())
    try:
        await mark_status(redis, round_id, STATUS_RUNNING)
        result = await execute_round(redis, payload, round_id=round_id)
        await mark_status(redis, round_id, STATUS_DONE, result=result)
        return {"status": "ok", "round_id": round_id, "result": result}
    except Exception as exc:  # noqa: BLE001 — 失败原因必须回到前端，不能只剩 traceback
        logger.exception("自动因子循环失败: round_id=%s", round_id)
        await _record_failure(redis, round_id, str(exc), mark_status, STATUS_ERROR)
        return {"status": "error", "round_id": round_id, "error": str(exc)}
    finally:
        await redis.aclose()


async def _record_failure(redis, round_id: str, message: str, mark, status: str) -> None:
    """把失败写进轮次记录。**写记录本身再失败也不能吞掉原始错误** —— 那才是要看的。"""
    try:
        await mark(redis, round_id, status, error=message)
    except Exception:  # noqa: BLE001
        logger.exception("写入自动因子循环失败状态时再次出错: round_id=%s", round_id)
