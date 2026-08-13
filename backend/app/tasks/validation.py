"""一键完整验证的 Celery 任务（V3 H2 的异步化补齐）

同步端点在默认参数（n_trials=24 / n_scenarios=500）下于日线 2–4 年区间是几十秒，
但寻优次数、蒙特卡洛场景数、回测区间都是用户可调的 —— 调大之后同步路径必然撞上
反向代理的网关超时，而那时用户拿到的是一个 504，跑了一半的算力全部作废。

因此提供异步入口：提交后立刻拿 `task_id`，轮询取结果。
同步入口保留不动 —— 短任务多一次轮询往返是纯粹的体验倒退。

与 `app/tasks/data.py` 同一约定：Celery worker 是同步进程，
异步取数经 `asyncio.run()` 桥接，且**自建 engine/session**
（worker 里没有 FastAPI 的 request context，拿不到 `Depends(get_db)` 那套）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)

#: 软/硬超时。完整验证比数据回填重得多，用全局默认（5/10 分钟）会在
#: 大参数空间下被 worker 杀掉 —— 那正是走异步想避免的失败模式。
_SOFT_TIME_LIMIT = 1800   # 30 分钟
_HARD_TIME_LIMIT = 2100   # 35 分钟


@shared_task(
    name="app.tasks.validation.run_full_validation_task",
    bind=True,
    soft_time_limit=_SOFT_TIME_LIMIT,
    time_limit=_HARD_TIME_LIMIT,
    # 完整验证是纯计算，重试只会把同样的输入再算一遍；
    # 真正的失败原因（数据不足、参数非法）重试也不会变好。
    max_retries=0,
)
def run_full_validation_task(self, payload: dict) -> dict:
    """异步执行一键完整验证。

    Args:
        payload: `FullValidationRequest.model_dump(mode="json")` 的结果

    Returns:
        `FullValidationOutcome.to_dict()`，与同步端点的返回体结构完全一致 ——
        前端两条路径共用同一套渲染代码。
    """
    try:
        return asyncio.run(_run(payload))
    except Exception as e:
        logger.exception("完整验证任务失败: %s", payload.get("symbol"))
        # 失败原因要能回到前端。Celery 默认会把异常序列化成 traceback 字符串，
        # 对用户毫无意义；这里给一个结构化的、可直接展示的结果。
        return {"status": "error", "error": str(e)}


async def _run(payload: dict) -> dict:
    """在 worker 进程内自建 DB 会话，取数后跑编排。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.api.v1.endpoints.backtest_full_validation import (
        FullValidationRequest,
        _make_runners,
        _validate_and_fetch,
    )
    from app.core.config import settings
    from app.data.service import DataService
    from app.engine.backtest.full_validation import run_full_validation

    body = FullValidationRequest(**payload)

    engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            market, bars = await _validate_and_fetch(body, DataService(session))
    finally:
        await engine.dispose()

    runners: dict[str, Any] = _make_runners(body, market, bars)
    outcome = run_full_validation(runners, body.steps)
    return {"status": "ok", **outcome.to_dict()}
