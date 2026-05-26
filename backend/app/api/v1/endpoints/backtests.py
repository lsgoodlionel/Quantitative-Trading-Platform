"""
回测 API 端点

支持两种模式：
1. 快速同步回测（数据量小，直接返回结果）
2. 异步任务回测（数据量大，提交到 Celery 队列，轮询结果）
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.models import Market, Frequency
from app.data.service import DataService
from app.engine.backtest.engine import BacktestEngine, BacktestConfig
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────

class BacktestRequest(BaseModel):
    strategy_name: str = Field(..., description="策略名称，见 /strategies/presets")
    symbol: str = Field(..., description="标的代码，如 AAPL 或 00700")
    market: str = Field("US", description="市场：US / HK / A")
    frequency: str = Field("1d", description="K 线周期：1m / 5m / 1d 等")
    start_date: date
    end_date: date
    initial_cash: float = Field(100_000.0, ge=1000)
    params: dict = Field(default_factory=dict, description="策略参数覆盖")


class BacktestMetricsResponse(BaseModel):
    total_return_pct: float
    annual_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    volatility_pct: float
    trading_days: int


class BacktestResponse(BaseModel):
    backtest_id: str
    strategy_name: str
    symbol: str
    market: str
    start_date: str
    end_date: str
    initial_cash: float
    final_value: float
    metrics: BacktestMetricsResponse
    equity_curve: list[dict]
    fills: list[dict]
    generated_at: str


# ── 依赖注入 ──────────────────────────────────────────────────

def get_service(session: AsyncSession = Depends(get_db)) -> DataService:
    return DataService(session)


# ── 端点 ─────────────────────────────────────────────────────

@router.post("/run", response_model=BacktestResponse)
async def run_backtest(
    body: BacktestRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> BacktestResponse:
    """
    同步执行回测并立即返回结果。
    适合日线数据 1-3 年的回测（通常 < 2 秒）。
    """
    # 1. 验证策略
    if body.strategy_name not in STRATEGY_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown strategy '{body.strategy_name}'. "
                   f"Available: {list(STRATEGY_REGISTRY.keys())}",
        )

    # 2. 验证市场
    try:
        market = Market(body.market.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid market '{body.market}'. Valid: US, HK, A",
        )

    # 3. 验证频率
    try:
        frequency = Frequency(body.frequency)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid frequency '{body.frequency}'.",
        )

    # 4. 获取历史数据
    try:
        bars = await svc.get_bars(
            symbol=body.symbol,
            market=market,
            frequency=frequency,
            start=body.start_date,
            end=body.end_date,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to fetch bars: {e}",
        )

    if len(bars) < 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient data: only {len(bars)} bars returned. "
                   "Check symbol, market, and date range.",
        )

    # 5. 运行回测
    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]
    strategy = strategy_cls(params=body.params)

    config = BacktestConfig(
        initial_cash=body.initial_cash,
        market=market,
    )
    engine = BacktestEngine(config)
    backtest_id = str(uuid.uuid4())

    try:
        result = engine.run(strategy, bars, strategy_id=backtest_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Backtest engine error: {e}",
        )

    # 6. 构建响应
    report = result.report
    return BacktestResponse(
        backtest_id=backtest_id,
        strategy_name=body.strategy_name,
        symbol=body.symbol,
        market=body.market,
        start_date=body.start_date.isoformat(),
        end_date=body.end_date.isoformat(),
        initial_cash=body.initial_cash,
        final_value=result.final_value,
        metrics=BacktestMetricsResponse(**report["metrics"]),
        equity_curve=report["equity_curve"],
        fills=report["fills"],
        generated_at=report["generated_at"],
    )


@router.get("/strategies")
async def list_strategies() -> list[dict]:
    """列出所有可用策略及其默认参数说明。"""
    return [
        {
            "name": name,
            "description": cls.description if hasattr(cls, "description") else "",
        }
        for name, cls in STRATEGY_REGISTRY.items()
    ]
