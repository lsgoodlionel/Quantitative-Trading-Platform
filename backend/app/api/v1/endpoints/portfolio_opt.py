"""
组合优化 API

POST /api/v1/portfolio/optimize
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as Date
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.models import Market, Frequency
from app.data.service import DataService
from app.engine.portfolio.optimizer import OptimizeMethod, optimize_portfolio
from app.engine.portfolio.risk_models import RiskModel
from app.engine.portfolio.expected_returns import ReturnsModel
from app.engine.portfolio.discrete_allocation import AllocationMethod, allocate

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────

class OptimizePortfolioRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=2, max_length=20)
    market: str = Field("US", description="US / HK / A")
    start_date: str = Field(..., description="YYYY-MM-DD")
    end_date: str = Field(..., description="YYYY-MM-DD")
    method: OptimizeMethod = OptimizeMethod.MAX_SHARPE
    include_frontier: bool = True
    # ── D1：风险模型 & 预期收益估计（带默认值，向后兼容）──
    risk_model: RiskModel = RiskModel.SAMPLE_COV
    expected_returns_method: ReturnsModel = ReturnsModel.MEAN_HISTORICAL

    @field_validator("symbols")
    @classmethod
    def upper_symbols(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]


class PortfolioOptResponse(BaseModel):
    method: str
    weights: dict[str, float]
    expected_return: float
    expected_volatility: float
    sharpe_ratio: float
    cvar_95: float
    frontier: list[dict]
    risk_contributions: dict[str, float]
    # ── D1 回显：所用估计器 ──
    risk_model: str
    expected_returns_method: str


# ── D2：离散配置 Schemas ──────────────────────────────────────

class AllocateRequest(BaseModel):
    weights: dict[str, float] = Field(..., description="symbol → 连续权重")
    latest_prices: dict[str, float] = Field(..., description="symbol → 最新价格")
    total_value: float = Field(..., gt=0, description="待配置现金预算")
    method: AllocationMethod = AllocationMethod.GREEDY

    @field_validator("weights")
    @classmethod
    def non_empty(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            raise ValueError("weights must be non-empty")
        if any(w < 0 for w in v.values()):
            raise ValueError("negative weights not supported (long-only)")
        return v


class AllocateResponse(BaseModel):
    method: str
    shares: dict[str, int]
    leftover_cash: float
    allocated_value: float
    total_value: float
    allocation_weights: dict[str, float]
    rmse: float
    skipped: list[str]


# ── 数据获取 ──────────────────────────────────────────────────

async def _fetch_prices(
    symbols: list[str],
    market_str: str,
    start_date: str,
    end_date: str,
    session: AsyncSession,
) -> pd.DataFrame:
    """并行拉取各标的日线收盘价，返回宽格式 DataFrame。"""
    try:
        market = Market(market_str.upper())
    except ValueError:
        raise HTTPException(400, detail=f"Invalid market: {market_str}")

    start = Date.fromisoformat(start_date)
    end = Date.fromisoformat(end_date)

    svc = DataService(session)

    async def fetch_one(sym: str) -> tuple[str, pd.Series]:
        try:
            bars = await svc.get_bars(
                symbol=sym,
                market=market,
                frequency=Frequency.DAY_1,
                start=start,
                end=end,
            )
            if not bars:
                raise RuntimeError(f"No data returned for {sym}")
            idx = [b.time for b in bars]
            prices = pd.Series([b.close for b in bars], index=idx, name=sym)
            return sym, prices
        except Exception as e:
            raise RuntimeError(f"Fetch failed for {sym}: {e}") from e

    tasks = [fetch_one(sym) for sym in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    series_dict: dict[str, pd.Series] = {}
    failed: list[str] = []
    for sym, res in zip(symbols, results):
        if isinstance(res, Exception):
            logger.warning("Could not fetch %s: %s", sym, res)
            failed.append(sym)
        else:
            _, series = res
            series_dict[sym] = series

    if len(series_dict) < 2:
        raise HTTPException(
            400,
            detail=f"Not enough data fetched (need ≥2 symbols). Failed: {failed}",
        )

    df = pd.DataFrame(series_dict).dropna()
    if len(df) < 60:
        raise HTTPException(
            400,
            detail=(
                f"Insufficient overlapping data: {len(df)} days (need ≥60). "
                "Widen the date range or check symbol validity."
            ),
        )
    return df


# ── 端点 ──────────────────────────────────────────────────────

@router.post("/optimize", response_model=PortfolioOptResponse)
async def optimize(
    body: OptimizePortfolioRequest,
    session: Annotated[AsyncSession, Depends(get_db)],
) -> PortfolioOptResponse:
    """
    组合权重优化。

    - **max_sharpe**: 最大化夏普比率
    - **min_volatility**: 最小化波动率
    - **risk_parity**: 风险平价（均等风险贡献）
    - **min_cvar**: 最小化 95% CVaR（条件风险价值）
    - **equal_weight**: 等权重基准对照
    """
    prices = await _fetch_prices(
        symbols=body.symbols,
        market_str=body.market,
        start_date=body.start_date,
        end_date=body.end_date,
        session=session,
    )

    try:
        result = await asyncio.to_thread(
            optimize_portfolio,
            prices,
            body.method,
            body.include_frontier,
            risk_model=body.risk_model,
            expected_returns_method=body.expected_returns_method,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Portfolio optimization failed")
        raise HTTPException(500, detail=f"Optimization failed: {e}")

    return PortfolioOptResponse(
        method=result.method,
        weights=result.weights,
        expected_return=result.expected_return,
        expected_volatility=result.expected_volatility,
        sharpe_ratio=result.sharpe_ratio,
        cvar_95=result.cvar_95,
        frontier=result.frontier,
        risk_contributions=result.risk_contributions,
        risk_model=result.risk_model,
        expected_returns_method=result.expected_returns_method,
    )


@router.post("/allocate", response_model=AllocateResponse)
async def allocate_portfolio(body: AllocateRequest) -> AllocateResponse:
    """
    离散配置：将连续权重按现金预算与最新价格转换为整数股数。

    - **greedy**: 贪心迭代（默认，无求解器，快速）
    - **lp**: 整数线性规划（scipy.optimize.milp，L1 最优）

    纯计算、无市场数据拉取；weights 中缺失价格的 symbol 计入 skipped。
    """
    try:
        result = await asyncio.to_thread(
            allocate,
            body.weights,
            body.latest_prices,
            body.total_value,
            body.method,
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        logger.exception("Discrete allocation failed")
        raise HTTPException(500, detail=f"Allocation failed: {e}")

    return AllocateResponse(
        method=result.method,
        shares=result.shares,
        leftover_cash=result.leftover_cash,
        allocated_value=result.allocated_value,
        total_value=result.total_value,
        allocation_weights=result.allocation_weights,
        rmse=result.rmse,
        skipped=result.skipped,
    )
