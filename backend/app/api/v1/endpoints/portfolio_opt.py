"""
组合优化 API

POST /api/v1/portfolio/optimize
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date as Date
from typing import Annotated, Literal

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.data.models import Market, Frequency
from app.data.service import DataService
from app.engine.portfolio.optimizer import OptimizeMethod, optimize_portfolio
from app.engine.portfolio.risk_models import RiskModel
from app.engine.portfolio.expected_returns import ReturnsModel
from app.engine.portfolio.discrete_allocation import AllocationMethod, allocate
from app.engine.portfolio.black_litterman import InvestorView, ViewKind
from app.engine.portfolio.cvar_opt import DEFAULT_BETA
from app.engine.portfolio.hrp import VALID_LINKAGE

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────

class BLViewInput(BaseModel):
    """
    单条 Black-Litterman 投资者观点。

    - absolute：assets=[sym]，value = 该标的年化预期收益（如 0.12 = 12%）
    - relative：assets=[long, short]，value = long 相对 short 的年化超额收益
    """
    kind: Literal["absolute", "relative"] = "absolute"
    assets: list[str] = Field(..., min_length=1, max_length=2)
    value: float = Field(..., ge=-1.0, le=2.0, description="年化收益/超额收益，小数形式")
    confidence: float = Field(0.5, ge=0.0, le=1.0, description="Idzorek 置信度 0~1")

    @field_validator("assets")
    @classmethod
    def upper_assets(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]

    @model_validator(mode="after")
    def check_arity(self) -> "BLViewInput":
        if self.kind == "absolute" and len(self.assets) != 1:
            raise ValueError("绝对观点必须且仅含 1 个标的")
        if self.kind == "relative" and len(self.assets) != 2:
            raise ValueError("相对观点必须含 2 个标的（long, short）")
        return self

    def to_view(self) -> InvestorView:
        return InvestorView(
            kind=ViewKind(self.kind),
            assets=tuple(self.assets),
            value=self.value,
            confidence=self.confidence,
        )


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
    # ── D3：Black-Litterman 观点（仅 method=black_litterman 使用）──
    views: list[BLViewInput] = Field(default_factory=list)
    market_caps: dict[str, float] | None = Field(
        None, description="symbol → 市值，缺省用等权代理"
    )
    bl_risk_aversion: float | None = Field(None, gt=0, description="BL 风险厌恶 δ，缺省 2.5")
    bl_tau: float = Field(0.05, gt=0, le=1.0, description="BL 先验不确定性缩放 τ")
    # ── D4：HRP 聚类连接方式 ──
    linkage_method: str = Field("single", description="HRP scipy 连接方式")
    # ── D5：CVaR/CDaR 置信水平 ──
    cvar_beta: float = Field(DEFAULT_BETA, gt=0.5, lt=1.0, description="尾部置信水平")

    @field_validator("symbols")
    @classmethod
    def upper_symbols(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]

    @field_validator("linkage_method")
    @classmethod
    def valid_linkage(cls, v: str) -> str:
        if v not in VALID_LINKAGE:
            raise ValueError(f"linkage_method 必须为 {VALID_LINKAGE} 之一")
        return v

    @model_validator(mode="after")
    def require_views_for_bl(self) -> "OptimizePortfolioRequest":
        if self.method == OptimizeMethod.BLACK_LITTERMAN and not self.views:
            raise ValueError("black_litterman 方法需要至少 1 条投资者观点（views）")
        return self


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
    # ── D3/D4/D5 回显（可选，按方法填充）──
    bl_prior_returns: dict[str, float] = Field(default_factory=dict)
    bl_posterior_returns: dict[str, float] = Field(default_factory=dict)
    bl_risk_aversion: float | None = None
    bl_views: list[str] = Field(default_factory=list)
    linkage_method: str | None = None
    cvar_beta: float | None = None


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
    - **min_cvar**: 最小化 95% CVaR（LP，条件风险价值）
    - **min_cdar**: 最小化条件回撤风险（LP）
    - **hrp**: 层次风险平价（相关性聚类 + 递归二分，无需求逆）
    - **black_litterman**: 市场均衡先验 + 投资者观点（需传 views）
    - **equal_weight**: 等权重基准对照
    """
    prices = await _fetch_prices(
        symbols=body.symbols,
        market_str=body.market,
        start_date=body.start_date,
        end_date=body.end_date,
        session=session,
    )

    views = [v.to_view() for v in body.views]

    try:
        result = await asyncio.to_thread(
            optimize_portfolio,
            prices,
            body.method,
            body.include_frontier,
            risk_model=body.risk_model,
            expected_returns_method=body.expected_returns_method,
            views=views,
            market_caps=body.market_caps,
            bl_risk_aversion=body.bl_risk_aversion,
            bl_tau=body.bl_tau,
            linkage_method=body.linkage_method,
            cvar_beta=body.cvar_beta,
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
        bl_prior_returns=result.bl_prior_returns,
        bl_posterior_returns=result.bl_posterior_returns,
        bl_risk_aversion=result.bl_risk_aversion,
        bl_views=result.bl_views,
        linkage_method=result.linkage_method,
        cvar_beta=result.cvar_beta,
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
