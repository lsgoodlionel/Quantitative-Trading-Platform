"""
组合再平衡执行 API（V3 Wave A-b · G2）

    POST /api/v1/portfolio/rebalance/preview   目标权重 → 股数级调仓明细 + 确认令牌
    POST /api/v1/portfolio/rebalance/execute   凭令牌逐腿走 OMS 下单

两步式的意义：预览与执行之间价格与持仓都会变，`confirm_token` 让服务端能拒绝
一份已经陈旧的预览，而不是照单下发到市场上。

挂载方式：本 router 被 `portfolio_opt.py` include 进 `/portfolio` 前缀
（`router.py` 属共享文件，本 Wave 不改；后续可直接在 router.py 注册）。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.v1.endpoints.orders import get_oms
from app.core.rbac import Role, require_role
from app.engine.portfolio.rebalance import (
    RebalanceLeg,
    plan_rebalance,
    validate_target_weights,
)
from app.oms.manager import OrderManager
from app.oms.rebalance_service import (
    ExecutionOutcome,
    current_positions,
    estimate_commission,
    execute_legs,
    load_snapshot,
)
from app.oms.rebalance_token import (
    REBALANCE_TOKEN_TTL_SECONDS,
    RebalanceTokenError,
    issue_token,
    verify_token,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_LEGS = 100


# ── Schemas ───────────────────────────────────────────────────

class RebalancePreviewRequest(BaseModel):
    target_weights: dict[str, float] = Field(..., min_length=1)
    market: str = Field("US", description="US / HK / A")
    min_trade_value: float = Field(0.0, ge=0, description="低于此金额的碎单直接剔除")
    lot_size: int = Field(1, ge=1, description="整手股数：港股/A 股常见 100")
    allow_short: bool = Field(False, description="允许负权重（做空腿）")


class RebalanceLegSchema(BaseModel):
    symbol: str
    current_qty: int
    target_qty: int
    delta_qty: int
    price: float
    delta_value: float
    reason: str
    side: str

    def to_leg(self) -> RebalanceLeg:
        return RebalanceLeg(
            symbol=self.symbol,
            current_qty=self.current_qty,
            target_qty=self.target_qty,
            delta_qty=self.delta_qty,
            price=self.price,
            delta_value=self.delta_value,
            reason=self.reason,
        )


class RebalancePreviewResponse(BaseModel):
    market: str
    portfolio_value: float
    legs: list[RebalanceLegSchema]
    total_buy_value: float
    total_sell_value: float
    estimated_commission: float
    warnings: list[str]
    confirm_token: str
    expires_in_seconds: int


class RebalanceExecuteRequest(BaseModel):
    market: str = Field("US", description="US / HK / A")
    legs: list[RebalanceLegSchema] = Field(..., min_length=1, max_length=_MAX_LEGS)
    confirm_token: str = Field(..., min_length=1)
    strategy_id: str | None = None


class RebalanceExecuteResponse(BaseModel):
    submitted: list[dict]
    rejected: list[dict]


# ── 端点 ──────────────────────────────────────────────────────

@router.post("/rebalance/preview", response_model=RebalancePreviewResponse)
async def preview_rebalance(
    body: RebalancePreviewRequest,
    oms: Annotated[OrderManager, Depends(get_oms)],
) -> RebalancePreviewResponse:
    """按目标权重算出股数级调仓明细，并签发一次性确认令牌。"""
    symbols = sorted(body.target_weights)
    try:
        validate_target_weights(body.target_weights, allow_short=body.allow_short)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    snapshot = await _load_snapshot_or_503(oms, body.market, symbols)

    if snapshot.portfolio_value <= 0:
        raise HTTPException(
            status_code=422,
            detail="账户净值为 0，无法计算目标股数（净值为 0 会把所有目标算成清仓）",
        )

    legs = plan_rebalance(
        target_weights=body.target_weights,
        current_qty=snapshot.current_qty,
        prices=snapshot.prices,
        portfolio_value=snapshot.portfolio_value,
        min_trade_value=body.min_trade_value,
        lot_size=body.lot_size,
    )
    if len(legs) > _MAX_LEGS:
        raise HTTPException(status_code=422, detail=f"调仓腿数超过上限 {_MAX_LEGS}")

    return RebalancePreviewResponse(
        market=snapshot.market,
        portfolio_value=round(snapshot.portfolio_value, 2),
        legs=[RebalanceLegSchema(**leg.to_dict()) for leg in legs],
        total_buy_value=round(sum(x.delta_value for x in legs if x.delta_qty > 0), 2),
        total_sell_value=round(abs(sum(x.delta_value for x in legs if x.delta_qty < 0)), 2),
        estimated_commission=estimate_commission(legs),
        warnings=list(snapshot.warnings),
        confirm_token=issue_token(snapshot.market, snapshot.current_qty, legs),
        expires_in_seconds=REBALANCE_TOKEN_TTL_SECONDS,
    )


@router.post("/rebalance/execute", response_model=RebalanceExecuteResponse)
async def execute_rebalance(
    body: RebalanceExecuteRequest,
    oms: Annotated[OrderManager, Depends(get_oms)],
    _user: Annotated[object, Depends(require_role(Role.TRADER))],
) -> RebalanceExecuteResponse:
    """
    执行调仓。令牌校验不通过一律拒绝，通过后逐腿走 `OrderManager.submit_order`。

    部分成功如实返回：`submitted` 与 `rejected` 可能同时非空，不做整批回滚。
    """
    legs = [item.to_leg() for item in body.legs]
    zero_qty = [leg.symbol for leg in legs if leg.delta_qty == 0]
    if zero_qty:
        raise HTTPException(status_code=400, detail=f"调仓量为 0 的腿: {zero_qty}")

    positions = await _positions_or_503(oms, body.market)
    try:
        verify_token(
            body.confirm_token,
            market=body.market,
            positions=positions,
            legs=legs,
        )
    except RebalanceTokenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    outcome: ExecutionOutcome = await execute_legs(
        oms, body.market, legs, strategy_id=body.strategy_id
    )
    logger.info(
        "再平衡执行完成 market=%s 成功=%d 失败=%d",
        body.market, len(outcome.submitted), len(outcome.rejected),
    )
    return RebalanceExecuteResponse(
        submitted=list(outcome.submitted),
        rejected=list(outcome.rejected),
    )


# ── 内部：把 OMS/券商故障翻译成明确的 HTTP 状态 ────────────────

async def _load_snapshot_or_503(oms: OrderManager, market: str, symbols: list[str]):
    try:
        return await load_snapshot(oms, market, symbols)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"拉取账户快照失败: {exc}"
        ) from exc


async def _positions_or_503(oms: OrderManager, market: str) -> dict[str, int]:
    try:
        return await current_positions(oms, market)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"拉取持仓失败: {exc}") from exc
