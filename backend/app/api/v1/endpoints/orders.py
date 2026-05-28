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
    market: str = Field(..., description="US / HK / A")
    side: str = Field(..., description="BUY / SELL")
    qty: int = Field(..., ge=1)
    order_type: str = Field("MARKET", description="MARKET / LIMIT")
    limit_price: Optional[float] = Field(None, ge=0)
    strategy_id: Optional[str] = None


class OrderResponse(BaseModel):
    order_id: str
    broker_order_id: Optional[str] = None
    paper_mode: bool = False           # True=模拟盘 False=实盘
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

def _try_get_oms() -> OrderManager | None:
    """返回 OMS 实例；未配置时返回 None。"""
    from app.oms.manager import get_order_manager
    try:
        return get_order_manager()
    except RuntimeError:
        return None


def get_oms() -> OrderManager:
    """仅写操作使用：OMS 未配置时返回 503。"""
    oms = _try_get_oms()
    if oms is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Order management system not available (live trading not configured)",
        )
    return oms


# ── 端点 ─────────────────────────────────────────────────────

@router.get("/trading-mode")
async def get_trading_mode() -> dict:
    """
    返回当前美股交易模式（模拟盘 / 实盘）。
    前端用于在订单页显示全局模式徽章。
    """
    try:
        import redis.asyncio as aioredis
        from app.core.config import settings
        r = aioredis.from_url(settings.redis_url)
        raw = await r.hgetall("broker_config:alpaca")
        await r.aclose()
        if not raw:
            return {"configured": False, "paper_mode": True, "mode_label": "未配置"}
        paper = raw.get(b"paper_mode", b"true").decode().lower() == "true"
        base_url = raw.get(b"base_url", b"").decode()
        return {
            "configured": True,
            "paper_mode": paper,
            "mode_label": "模拟盘 (Paper)" if paper else "实盘 (Live)",
            "base_url": base_url,
        }
    except Exception:
        return {"configured": False, "paper_mode": True, "mode_label": "未知"}


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
    strategy_id: Optional[str] = Query(None),
    order_status: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
) -> list[OrderResponse]:
    """查询订单列表。未配置券商时返回空列表。"""
    oms = _try_get_oms()
    if oms is None:
        return []
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
        paper_mode=getattr(order, "paper_mode", False),
        created_at=order.created_at.isoformat(),
        filled_at=order.filled_at.isoformat() if order.filled_at else None,
    )


@router.get("/attribution")
async def get_performance_attribution(
    market: Optional[str] = Query(None, description="筛选市场: US / HK / A"),
) -> dict:
    """
    持仓绩效归因分析。

    基于已成交订单计算各标的的已实现盈亏、交易统计。
    """
    oms = _try_get_oms()
    if oms is None:
        return {"positions": [], "totals": {}}

    all_orders = oms.list_orders(limit=10000)

    # Filter filled orders
    from app.oms.order import LiveOrderStatus, LiveOrderSide
    filled = [
        o for o in all_orders
        if o.status == LiveOrderStatus.FILLED
        and (market is None or o.market.upper() == market.upper())
    ]

    # Group by symbol
    from collections import defaultdict
    sym_data: dict = defaultdict(lambda: {
        "symbol": "", "market": "",
        "buy_qty": 0, "sell_qty": 0,
        "buy_value": 0.0, "sell_value": 0.0,
        "commission": 0.0,
        "trade_count": 0,
    })

    for o in filled:
        key = f"{o.market}:{o.symbol}"
        d = sym_data[key]
        d["symbol"]  = o.symbol
        d["market"]  = o.market
        d["trade_count"] += 1
        d["commission"]  += o.commission
        if o.side == LiveOrderSide.BUY:
            d["buy_qty"]   += o.filled_qty
            d["buy_value"] += (o.avg_fill_price or 0) * o.filled_qty
        else:
            d["sell_qty"]   += o.filled_qty
            d["sell_value"] += (o.avg_fill_price or 0) * o.filled_qty

    # Compute realized P&L and net position
    # For partially closed positions: realized = sell_value - (buy_value / buy_qty) * sell_qty
    attribution = []
    for d in sym_data.values():
        avg_buy = d["buy_value"] / d["buy_qty"] if d["buy_qty"] > 0 else 0.0
        realized_qty = min(d["buy_qty"], d["sell_qty"])
        realized_pnl = (d["sell_value"] - avg_buy * realized_qty) - d["commission"]
        net_qty = d["buy_qty"] - d["sell_qty"]

        attribution.append({
            "symbol":       d["symbol"],
            "market":       d["market"],
            "buy_qty":      d["buy_qty"],
            "sell_qty":     d["sell_qty"],
            "net_qty":      net_qty,
            "buy_value":    round(d["buy_value"], 2),
            "sell_value":   round(d["sell_value"], 2),
            "avg_buy_cost": round(avg_buy, 4),
            "commission":   round(d["commission"], 4),
            "realized_pnl": round(realized_pnl, 2),
            "trade_count":  d["trade_count"],
        })

    attribution.sort(key=lambda x: x["realized_pnl"], reverse=True)

    total_realized = sum(a["realized_pnl"] for a in attribution)
    total_commission = sum(a["commission"] for a in attribution)
    total_trades = sum(a["trade_count"] for a in attribution)

    return {
        "positions": attribution,
        "totals": {
            "realized_pnl": round(total_realized, 2),
            "commission":   round(total_commission, 4),
            "trade_count":  total_trades,
            "symbol_count": len(attribution),
        },
    }
