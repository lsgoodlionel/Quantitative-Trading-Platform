"""公式因子 → 策略适配器 API 端点（V3 Wave A-a / G1）

  POST   /factors/strategy/backtest   — spec + 区间 → 组合回测（无需先注册）
  POST   /factors/strategy/promote    — spec + name → 存为命名策略
  GET    /factors/strategy            — 列出已注册的因子策略
  GET    /factors/strategy/methods    — 可选的组合权重方法（供前端下拉框）
  DELETE /factors/strategy/{name}     — 删除一条命名策略

「一键回测」= 前端把因子库条目 / 挖掘结果的公式填进 spec 直接打第一个端点，
不必先注册 —— 看到一个因子，立刻知道它能不能赚钱。

风格对齐 endpoints/factor_mining.py：Pydantic v2 请求模型 + try/except →
HTTPException(400/404/422)，数据层复用 AsyncSessionLocal + DataService，
重型模块在 handler 内惰性 import，Redis 走 core.redis.get_redis。
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.redis import get_redis

router = APIRouter(tags=["Factor Strategy"])

#: 单标的最少 bar 数（低于则从标的池剔除）
MIN_BARS = 60
#: 有效标的下限 —— 截面因子至少要能比较两个标的
MIN_SYMBOLS = 2
#: 净值曲线最多返回的点数（避免 payload 过大）
MAX_CURVE_POINTS = 1_000
#: 成交明细最多返回的条数
MAX_FILLS = 500


# ── 请求模型 ──────────────────────────────────────────────────────


class SpecModel(BaseModel):
    """`FactorStrategySpec` 的 API 形态（字段与语义一一对应）。"""

    formula: str = Field(min_length=1, max_length=400, description="RPN 表达式，空格分隔")
    universe: list[str] = Field(min_length=2, max_length=60)
    long_quantile: float = Field(default=0.2, gt=0.0, le=1.0, description="做多分数最高的比例")
    short_quantile: float | None = Field(default=None, ge=0.0, lt=1.0)
    rebalance_days: int = Field(default=5, ge=1, le=250)
    portfolio_method: str = "equal_weight"
    max_positions: int | None = Field(default=None, ge=1, le=100)

    def to_spec(self):
        """转成领域对象；spec 自身的校验失败即 400（用户输入错误）。"""
        from app.strategy.factor_strategy import FactorStrategySpec

        try:
            return FactorStrategySpec(**self.model_dump())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e


class BacktestRequest(BaseModel):
    spec: SpecModel
    market: Literal["US", "HK", "A"] = "US"
    frequency: str = "1d"
    start: str | None = None
    end: str | None = None
    initial_cash: float = Field(default=1_000_000.0, gt=0)
    allow_short: bool = False


class PromoteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    spec: SpecModel
    note: str = Field(default="", max_length=500)
    #: 来源实验记录（可选，仅溯源；策略独立存完整 spec，不依赖它存活）
    experiment_id: str | None = Field(default=None, max_length=64)


# ── 数据拉取（自包含，避免跨端点耦合）─────────────────────────────


async def _fetch_universe(req: BacktestRequest) -> dict[str, list]:
    """按标的池拉 bar；剔除失败或 bar 数不足者。"""
    from datetime import date, timedelta

    from app.core.database import AsyncSessionLocal
    from app.data.models import Frequency as FreqEnum
    from app.data.models import Market as MarketEnum
    from app.data.service import DataService

    end_date = date.fromisoformat(req.end) if req.end else date.today()
    start_date = date.fromisoformat(req.start) if req.start else end_date - timedelta(days=365 * 2)

    try:
        market_enum = MarketEnum(req.market)
        freq_enum = FreqEnum(req.frequency)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    bars_by_symbol: dict[str, list] = {}
    async with AsyncSessionLocal() as session:
        svc = DataService(session)
        for symbol in req.spec.universe:
            sym = symbol.strip().upper()
            if not sym:
                continue
            try:
                bars = await svc.get_bars(sym, market_enum, freq_enum, start_date, end_date)
            except Exception:
                continue                    # 单标的失败不作硬错误，剔除即可
            if len(bars) >= MIN_BARS:
                bars_by_symbol[sym] = bars

    if len(bars_by_symbol) < MIN_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"有效标的不足（截面因子需 ≥ {MIN_SYMBOLS}，实得 {len(bars_by_symbol)}；"
                   f"每标的需 ≥ {MIN_BARS} 根 bar）",
        )
    return bars_by_symbol


def _run_backtest(spec, bars_by_symbol: dict[str, list], req: BacktestRequest):
    """同步的重活，交给 executor 跑，别堵事件循环。"""
    from app.data.models import Market as MarketEnum
    from app.engine.backtest.portfolio_engine import (
        PortfolioBacktestConfig,
        PortfolioBacktestEngine,
    )
    from app.strategy.factor_strategy import build_factor_strategy

    config = PortfolioBacktestConfig(
        initial_cash=req.initial_cash,
        market=MarketEnum(req.market),
        allow_short=req.allow_short,
    )
    strategy = build_factor_strategy(spec)
    return PortfolioBacktestEngine(config).run(strategy, bars_by_symbol, "factor-strategy")


def _serialize_result(result, spec) -> dict:
    from app.engine.backtest.report import metrics_to_dict

    curve = result.equity_curve.tail(MAX_CURVE_POINTS)
    return {
        "spec": spec.to_dict(),
        "symbols": result.symbols,
        "strategy_name": result.strategy_name,
        "start_date": result.start_date.isoformat(),
        "end_date": result.end_date.isoformat(),
        "initial_cash": result.initial_cash,
        "final_value": round(result.final_value, 4),
        "metrics": metrics_to_dict(result.metrics),
        "equity_curve": [
            {"date": ts.isoformat(), "value": round(float(v), 4)}
            for ts, v in curve.items()
        ],
        "n_fills": len(result.fills),
        "fills": result.fills[-MAX_FILLS:],
    }


# ── 端点：一键回测 ────────────────────────────────────────────────


@router.post("/strategy/backtest")
async def backtest_factor_strategy(req: BacktestRequest) -> dict:
    """公式因子 → 组合回测，返回净值曲线与绩效指标（无需先注册策略）。"""
    spec = req.spec.to_spec()
    bars_by_symbol = await _fetch_universe(req)

    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _run_backtest, spec, bars_by_symbol, req)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"因子策略回测失败: {e}") from e

    return _serialize_result(result, spec)


# ── 端点：命名策略 CRUD ───────────────────────────────────────────


@router.post("/strategy/promote", status_code=status.HTTP_201_CREATED)
async def promote_factor_strategy(
    req: PromoteRequest,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """把一条 spec 存成命名策略；给了 experiment_id 则同时回写实验记录。"""
    from app.quant.experiments.recorder import promote_to_strategy
    from app.strategy.factor_store import get_factor_strategy, save_factor_strategy

    spec = req.spec.to_spec()
    try:
        if req.experiment_id:
            name = await promote_to_strategy(redis, req.experiment_id, req.name, spec)
            record = await get_factor_strategy(redis, name)
        else:
            record = await save_factor_strategy(redis, name=req.name, spec=spec, note=req.note)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if record is None:      # 刚写完就读不到 ⇒ 存储异常，不能假装成功
        raise HTTPException(status_code=422, detail="策略已写入但无法读回，请重试")
    return record.to_dict()


@router.get("/strategy")
async def list_factor_strategies_endpoint(
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> dict:
    """列出已注册的因子策略（每条都带完整 spec，可直接重跑）。"""
    from app.strategy.factor_store import list_factor_strategies

    records = await list_factor_strategies(redis)
    return {"count": len(records), "strategies": [r.to_dict() for r in records]}


@router.get("/strategy/methods")
async def list_portfolio_methods() -> dict:
    """可选的组合权重方法（供前端下拉框，避免前后端各写一份常量）。"""
    from app.strategy.factor_strategy import PORTFOLIO_METHODS

    return {"methods": list(PORTFOLIO_METHODS)}


@router.delete("/strategy/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_factor_strategy_endpoint(
    name: str,
    redis: Annotated[aioredis.Redis, Depends(get_redis)],
) -> None:
    """删除一条命名因子策略。"""
    from app.strategy.factor_store import delete_factor_strategy

    try:
        existed = await delete_factor_strategy(redis, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not existed:
        raise HTTPException(status_code=404, detail="因子策略不存在")
