"""实盘持仓 API"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────

class PositionResponse(BaseModel):
    symbol: str
    market: str
    qty: int
    avg_cost: float
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None


class AccountResponse(BaseModel):
    account_id: str
    currency: str
    cash: float
    buying_power: float
    portfolio_value: float


# ── Demo data (shown when no live broker is configured) ──────

_DEMO_ACCOUNT = AccountResponse(
    account_id="DEMO-001",
    currency="USD",
    cash=85_430.00,
    buying_power=170_860.00,
    portfolio_value=100_430.00,
)

_DEMO_POSITIONS: list[PositionResponse] = [
    PositionResponse(
        symbol="AAPL", market="US", qty=50,
        avg_cost=178.20, current_price=189.50,
        market_value=9_475.00, unrealized_pnl=565.00, unrealized_pnl_pct=0.0634,
    ),
    PositionResponse(
        symbol="MSFT", market="US", qty=30,
        avg_cost=415.80, current_price=432.10,
        market_value=12_963.00, unrealized_pnl=489.00, unrealized_pnl_pct=0.0392,
    ),
    PositionResponse(
        symbol="NVDA", market="US", qty=10,
        avg_cost=875.40, current_price=906.80,
        market_value=9_068.00, unrealized_pnl=314.00, unrealized_pnl_pct=0.0359,
    ),
    PositionResponse(
        symbol="TSLA", market="US", qty=20,
        avg_cost=195.60, current_price=177.30,
        market_value=3_546.00, unrealized_pnl=-366.00, unrealized_pnl_pct=-0.0936,
    ),
]


# ── Helpers ───────────────────────────────────────────────────

def _try_get_oms():
    """返回 OMS 实例；未配置时返回 None（不抛 503）。"""
    from app.oms.manager import get_order_manager
    try:
        return get_order_manager()
    except RuntimeError:
        return None


# ── Endpoints ─────────────────────────────────────────────────

@router.get("", response_model=list[PositionResponse])
async def list_positions(
    market: str = Query("US", description="US / HK"),
) -> list[PositionResponse]:
    """查询实盘持仓。未配置券商时返回演示数据。"""
    oms = _try_get_oms()
    if oms is None:
        return [p for p in _DEMO_POSITIONS if p.market == market.upper()]

    try:
        positions = await oms.get_positions(market.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch positions: {e}")

    return [PositionResponse(**p) for p in positions]


@router.get("/account", response_model=AccountResponse)
async def get_account(
    market: str = Query("US", description="US / HK"),
) -> AccountResponse:
    """查询账户资金状态。未配置券商时返回演示数据。"""
    oms = _try_get_oms()
    if oms is None:
        return _DEMO_ACCOUNT

    try:
        account = await oms.get_account(market.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch account: {e}")

    return AccountResponse(**account)
