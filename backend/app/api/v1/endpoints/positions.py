"""实盘持仓 API"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.oms.manager import OrderManager

router = APIRouter()


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


def get_oms() -> OrderManager:
    from app.oms.manager import get_order_manager
    try:
        return get_order_manager()
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail="Order management system not available",
        )


@router.get("", response_model=list[PositionResponse])
async def list_positions(
    oms: Annotated[OrderManager, Depends(get_oms)],
    market: str = Query("US", description="US / HK"),
) -> list[PositionResponse]:
    """查询实盘持仓（从券商实时拉取）。"""
    try:
        positions = await oms.get_positions(market.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch positions: {e}")

    return [PositionResponse(**p) for p in positions]


@router.get("/account", response_model=AccountResponse)
async def get_account(
    oms: Annotated[OrderManager, Depends(get_oms)],
    market: str = Query("US", description="US / HK"),
) -> AccountResponse:
    """查询账户资金状态。"""
    try:
        account = await oms.get_account(market.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch account: {e}")

    return AccountResponse(**account)
