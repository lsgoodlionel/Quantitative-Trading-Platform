"""
批量回测 API（V3 G3：Screener 多选贯通）

POST /api/v1/backtests/batch
    { symbols, strategy_name, params, start_date, end_date, market, frequency }
  → { results: [{symbol, metrics, final_value, error?}], ... }

两条硬约束：
1. 标的数量上限 MAX_BATCH_SYMBOLS —— 一次请求跑几百个回测会把服务打满。
2. 单个标的失败**不得**让整批 500 —— 失败项在 results 里带 error 字段返回。

串行执行（并行化见契约「不做」清单）。挂载在 backtests.py 的路由下，
故无需改动共享的 router.py。
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.service import DataService
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.notify.emit import emit_batch_backtest_done
from app.strategy.presets import STRATEGY_REGISTRY

logger = logging.getLogger(__name__)

router = APIRouter()

# 单次批量回测的标的上限
MAX_BATCH_SYMBOLS = 50


# ── 依赖注入（与 backtests.py 同构，独立定义以免与其形成导入环）──

def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


# ── Schemas ──────────────────────────────────────────────────

class BatchBacktestRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, description="标的代码列表")
    strategy_name: str = Field(..., description="策略名称，见 /strategies/presets")
    market: str = Field("US", description="市场：US / HK / A")
    frequency: str = Field("1d", description="K 线周期")
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数覆盖")

    @field_validator("symbols")
    @classmethod
    def normalize_symbols(cls, v: list[str]) -> list[str]:
        """去空白 + 去重（保序）；空列表交由 min_length 拦截。"""
        seen: list[str] = []
        for raw in v:
            symbol = raw.strip().upper()
            if symbol and symbol not in seen:
                seen.append(symbol)
        return seen


class BatchBacktestItem(BaseModel):
    symbol: str
    metrics: dict | None = None
    final_value: float | None = None
    error: str | None = None


class BatchBacktestResponse(BaseModel):
    batch_id: str
    strategy_name: str
    market: str
    total: int
    succeeded: int
    failed: int
    results: list[BatchBacktestItem]


# ── 内部工具 ─────────────────────────────────────────────────

def _validate_batch_request(body: BatchBacktestRequest) -> None:
    """整批级别的前置校验：策略存在 + 标的数量在上限内。"""
    if body.strategy_name not in STRATEGY_REGISTRY:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{body.strategy_name}'. "
                   f"Available: {list(STRATEGY_REGISTRY.keys())}",
        )
    if not body.symbols:
        raise HTTPException(status_code=400, detail="symbols 不能为空")
    if len(body.symbols) > MAX_BATCH_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"批量回测最多支持 {MAX_BATCH_SYMBOLS} 个标的，"
                   f"当前 {len(body.symbols)} 个，请缩小选择范围",
        )


async def _run_one(
    body: BatchBacktestRequest, symbol: str, svc: DataService
) -> BatchBacktestItem:
    """
    跑单个标的。任何失败都收敛为带 error 的结果项，绝不上抛。

    这是「单标的失败不拖垮整批」的落点：数据缺失、策略异常、引擎报错
    都只影响该标的自己。
    """
    from app.api.v1.endpoints.backtests import _validate_and_fetch

    try:
        market, _frequency, bars = await _validate_and_fetch(
            body.strategy_name, body.market, body.frequency,
            body.start_date, body.end_date, svc, symbol,
        )
    except HTTPException as e:
        return BatchBacktestItem(symbol=symbol, error=str(e.detail))
    except Exception as e:  # noqa: BLE001 — 批量场景下单标的异常必须隔离
        logger.exception("批量回测取数失败 · symbol=%s", symbol)
        return BatchBacktestItem(symbol=symbol, error=f"数据加载失败: {e}")

    try:
        strategy = STRATEGY_REGISTRY[body.strategy_name](params=body.params)
        engine = BacktestEngine(BacktestConfig(initial_cash=body.initial_cash, market=market))
        result = engine.run(strategy, bars)
    except Exception as e:  # noqa: BLE001 — 同上
        logger.exception("批量回测执行失败 · symbol=%s", symbol)
        return BatchBacktestItem(symbol=symbol, error=f"回测执行失败: {e}")

    return BatchBacktestItem(
        symbol=symbol,
        metrics=result.report["metrics"],
        final_value=result.final_value,
    )


# ── 端点 ─────────────────────────────────────────────────────

@router.post("/batch", response_model=BatchBacktestResponse)
async def run_batch_backtest(
    body: BatchBacktestRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> BatchBacktestResponse:
    """对一组标的用同一策略串行跑回测；失败项带 error 返回，不影响其余标的。"""
    _validate_batch_request(body)

    results = [await _run_one(body, symbol, svc) for symbol in body.symbols]
    succeeded = sum(1 for r in results if r.error is None)

    emit_batch_backtest_done(
        strategy_name=body.strategy_name,
        market=body.market,
        symbols=body.symbols,
        succeeded=succeeded,
        failed=len(results) - succeeded,
    )

    return BatchBacktestResponse(
        batch_id=str(uuid.uuid4()),
        strategy_name=body.strategy_name,
        market=body.market,
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )
