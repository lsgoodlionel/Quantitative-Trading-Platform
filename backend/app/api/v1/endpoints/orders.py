"""实盘订单 API"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.oms.manager import OrderManager, RiskViolation
from app.oms.order import LiveOrderSide, LiveOrderType

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────

class SubmitOrderRequest(BaseModel):
    symbol: str
    market: str = Field(..., description="US / HK")
    side: str = Field(..., description="BUY / SELL")
    qty: int = Field(..., ge=1)
    order_type: str = Field("MARKET", description="MARKET / LIMIT")
    limit_price: Optional[float] = Field(None, ge=0)
    strategy_id: Optional[str] = None


class OrderResponse(BaseModel):
    order_id: str
    broker_order_id: Optional[str] = None
    strategy_id: Optional[str] = None
    symbol: str
    market: str
    side: str
    qty: int
    order_type: str
    limit_price: Optional[float] = None
    status: str
    filled_qty: int
    avg_fill_price: Optional[float] = None
    commission: float
    reject_reason: Optional[str] = None
    created_at: str
    filled_at: Optional[str] = None


# ── 依赖注入 ──────────────────────────────────────────────────

def get_oms() -> OrderManager:
    from app.oms.manager import get_order_manager
    try:
        return get_order_manager()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Order management system not available (live trading not configured)",
        )


# ── 端点 ─────────────────────────────────────────────────────

@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def submit_order(
    body: SubmitOrderRequest,
    oms: Annotated[OrderManager, Depends(get_oms)],
) -> OrderResponse:
    """提交实盘订单。"""
    try:
        side = LiveOrderSide(body.side.upper())
    except ValueError:
        raise HTTPException(400, detail=f"Invalid side '{body.side}'. Use BUY or SELL.")

    try:
        order_type = LiveOrderType(body.order_type.upper())
    except ValueError:
        raise HTTPException(400, detail=f"Invalid order_type '{body.order_type}'.")

    try:
        order = await oms.submit_order(
            symbol=body.symbol,
            market=body.market.upper(),
            side=side,
            qty=body.qty,
            order_type=order_type,
            limit_price=body.limit_price,
            strategy_id=body.strategy_id,
        )
    except RiskViolation as e:
        raise HTTPException(status_code=422, detail=f"Risk violation: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return _to_response(order)


@router.get("", response_model=list[OrderResponse])
async def list_orders(
    oms: Annotated[OrderManager, Depends(get_oms)],
    strategy_id: Optional[str] = Query(None),
    order_status: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[OrderResponse]:
    """查询订单列表。"""
    orders = oms.list_orders(strategy_id=strategy_id, status=order_status, limit=limit)
    return [_to_response(o) for o in orders]


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: str,
    oms: Annotated[OrderManager, Depends(get_oms)],
) -> OrderResponse:
    """查询单个订单详情。"""
    order = oms.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return _to_response(order)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: str,
    oms: Annotated[OrderManager, Depends(get_oms)],
) -> OrderResponse:
    """撤销指定订单。"""
    try:
        order = await oms.cancel_order(order_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Cancel failed: {e}")
    return _to_response(order)


def _to_response(order) -> OrderResponse:
    return OrderResponse(
        order_id=order.order_id,
        broker_order_id=order.broker_order_id,
        strategy_id=order.strategy_id,
        symbol=order.symbol,
        market=order.market,
        side=order.side.value,
        qty=order.qty,
        order_type=order.order_type.value,
        limit_price=order.limit_price,
        status=order.status.value,
        filled_qty=order.filled_qty,
        avg_fill_price=order.avg_fill_price,
        commission=order.commission,
        reject_reason=order.reject_reason,
        created_at=order.created_at.isoformat(),
        filled_at=order.filled_at.isoformat() if order.filled_at else None,
    )
