"""风控 API 端点"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.risk.engine import get_risk_engine, init_risk_engine
from app.risk.models import (
    RiskConfig,
    RiskRule,
    RuleType,
    ViolationSeverity,
    default_risk_config,
)
from app.risk.portfolio import OptimizeMode, optimize_portfolio, compute_rebalance

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────

class RiskRuleSchema(BaseModel):
    rule_type: str
    value: Any
    enabled: bool = True
    severity: str = "block"


class RiskConfigSchema(BaseModel):
    name: str = "default"
    rules: list[RiskRuleSchema]
    is_active: bool = True


class RiskConfigResponse(BaseModel):
    name: str
    rules: list[RiskRuleSchema]
    is_active: bool


class PreTradeCheckRequest(BaseModel):
    symbol: str
    market: str
    side: str
    qty: int
    price: float
    portfolio_value: float
    current_symbol_value: float = 0.0


class PreTradeCheckResponse(BaseModel):
    passed: bool
    violations: list[dict]


class PortfolioCheckRequest(BaseModel):
    portfolio_value: float
    initial_value: float
    positions: list[dict] = Field(default_factory=list)


class OptimizeRequest(BaseModel):
    prices: dict[str, list[float]]  # symbol → 收盘价序列（按时间升序）
    mode: str = "max_sharpe"
    risk_free_rate: float = 0.04
    max_weight: float = 0.4


class RebalanceRequest(BaseModel):
    current_positions: dict[str, float]  # symbol → market_value
    target_weights: dict[str, float]     # symbol → weight (0~1)
    portfolio_value: float
    min_trade_value: float = 500.0


# ── 端点 ─────────────────────────────────────────────────────

@router.get("", response_model=RiskConfigResponse)
async def get_risk_config() -> RiskConfigResponse:
    """获取当前风控配置。"""
    engine = get_risk_engine()
    cfg = engine.config
    return RiskConfigResponse(
        name=cfg.name,
        rules=[RiskRuleSchema(**r.to_dict()) for r in cfg.rules],
        is_active=cfg.is_active,
    )


@router.put("", response_model=RiskConfigResponse, status_code=status.HTTP_200_OK)
async def update_risk_config(body: RiskConfigSchema) -> RiskConfigResponse:
    """热更新风控配置（立即生效）。"""
    try:
        rules = [
            RiskRule(
                rule_type=RuleType(r.rule_type),
                value=r.value,
                enabled=r.enabled,
                severity=ViolationSeverity(r.severity),
            )
            for r in body.rules
        ]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid rule config: {e}")

    new_config = RiskConfig(name=body.name, rules=rules, is_active=body.is_active)
    engine = get_risk_engine()
    engine.update_config(new_config)

    return RiskConfigResponse(
        name=new_config.name,
        rules=[RiskRuleSchema(**r.to_dict()) for r in new_config.rules],
        is_active=new_config.is_active,
    )


@router.post("/check/pre-trade", response_model=PreTradeCheckResponse)
async def pre_trade_check(body: PreTradeCheckRequest) -> PreTradeCheckResponse:
    """前置风控检查（下单前调用）。"""
    engine = get_risk_engine()
    violations = engine.pre_trade_check(
        symbol=body.symbol,
        market=body.market,
        side=body.side.upper(),
        qty=body.qty,
        price=body.price,
        portfolio_value=body.portfolio_value,
        current_symbol_value=body.current_symbol_value,
    )
    block_violations = [v for v in violations if v.severity in (ViolationSeverity.BLOCK, ViolationSeverity.HALT)]
    return PreTradeCheckResponse(
        passed=len(block_violations) == 0,
        violations=[v.to_dict() for v in violations],
    )


@router.post("/check/portfolio")
async def portfolio_check(body: PortfolioCheckRequest) -> dict:
    """实时组合风控检查。"""
    engine = get_risk_engine()
    violations = engine.portfolio_check(
        portfolio_value=body.portfolio_value,
        initial_value=body.initial_value,
        positions=body.positions,
    )
    return {
        "passed": all(v.severity == ViolationSeverity.WARNING for v in violations),
        "violations": [v.to_dict() for v in violations],
        "daily_summary": engine.daily_summary(),
    }


@router.get("/summary")
async def risk_summary() -> dict:
    """获取当日风控统计摘要。"""
    engine = get_risk_engine()
    return engine.daily_summary()


@router.post("/portfolio/optimize")
async def optimize_portfolio_endpoint(body: OptimizeRequest) -> dict:
    """
    组合权重优化。
    prices: {symbol: [price1, price2, ...]} 按时间升序
    """
    import pandas as pd

    if not body.prices or len(body.prices) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 symbols")

    lengths = [len(v) for v in body.prices.values()]
    if min(lengths) < 30:
        raise HTTPException(status_code=400, detail="Need at least 30 price points per symbol")

    if len(set(lengths)) > 1:
        # 截断到最短序列
        min_len = min(lengths)
        price_data = {k: v[-min_len:] for k, v in body.prices.items()}
    else:
        price_data = body.prices

    prices_df = pd.DataFrame(price_data)

    try:
        mode: OptimizeMode = body.mode  # type: ignore[assignment]
        result = optimize_portfolio(
            prices=prices_df,
            mode=mode,
            risk_free_rate=body.risk_free_rate,
            weight_bounds=(0.0, body.max_weight),
        )
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Optimization failed: {e}")

    return result.to_dict()


@router.post("/portfolio/rebalance")
async def compute_rebalance_endpoint(body: RebalanceRequest) -> dict:
    """
    计算再平衡指令。
    返回需要买入/卖出的标的和金额。
    """
    orders = compute_rebalance(
        current_positions=body.current_positions,
        target_weights=body.target_weights,
        portfolio_value=body.portfolio_value,
        min_trade_value=body.min_trade_value,
    )
    return {
        "portfolio_value": body.portfolio_value,
        "orders": [
            {
                "symbol": o.symbol,
                "current_weight_pct": round(o.current_weight * 100, 2),
                "target_weight_pct": round(o.target_weight * 100, 2),
                "delta_weight_pct": round(o.delta_weight * 100, 2),
                "delta_value": o.delta_value,
                "action": "BUY" if o.delta_value > 0 else "SELL",
            }
            for o in orders
        ],
        "total_buy": round(sum(o.delta_value for o in orders if o.delta_value > 0), 2),
        "total_sell": round(abs(sum(o.delta_value for o in orders if o.delta_value < 0)), 2),
    }
