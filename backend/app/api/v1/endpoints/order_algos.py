"""
高级订单算法 API（E3）

- POST /orders/algo          提交 TWAP / VWAP / 冰山算法单（父单拆子单）
- GET  /orders/algo          列出算法单
- GET  /orders/algo/{id}     查询单个算法单进度
- POST /orders/algo/{id}/cancel  撤销算法单（停止后续切片，不回撤已成交子单）

子单统一走现有 OMS.submit_order（E4 一致性：同生命周期/事件/风控）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.oms.algos.executor import AlgoValidationError, get_algo_executor
from app.oms.algos.base import AlgoType

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────

class SubmitAlgoRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    market: str = Field(..., description="US / HK / A")
    side: str = Field(..., description="BUY / SELL")
    algo_type: str = Field(..., description="TWAP / VWAP / ICEBERG")
    total_qty: int = Field(..., ge=1, description="父单总股数")

    order_type: str = Field("MARKET", description="子单类型 MARKET / LIMIT")
    limit_price: Optional[float] = Field(None, gt=0)
    strategy_id: Optional[str] = None

    duration_seconds: float = Field(300.0, gt=0, description="总执行时长（秒）")
    slice_count: int = Field(6, ge=1, le=100, description="TWAP/VWAP 切片数")
    display_qty: Optional[int] = Field(None, gt=0, description="冰山单每次露出股数")


class ChildSliceResponse(BaseModel):
    index: int
    qty: int
    delay_seconds: float
    status: str
    child_order_id: Optional[str] = None
    filled_qty: int
    avg_fill_price: Optional[float] = None
    error: Optional[str] = None
    submitted_at: Optional[str] = None


class AlgoOrderResponse(BaseModel):
    algo_id: str
    algo_type: str
    symbol: str
    market: str
    side: str
    total_qty: int
    order_type: str
    limit_price: Optional[float] = None
    strategy_id: Optional[str] = None
    duration_seconds: float
    slice_count: int
    display_qty: Optional[int] = None
    status: str
    filled_qty: int
    submitted_qty: int
    avg_fill_price: Optional[float] = None
    progress_pct: float
    slices: list[ChildSliceResponse]
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_at: str


# ── 端点 ─────────────────────────────────────────────────────

@router.post("/algo", response_model=AlgoOrderResponse, status_code=status.HTTP_201_CREATED)
async def submit_algo_order(body: SubmitAlgoRequest) -> AlgoOrderResponse:
    """提交高级订单算法单。"""
    try:
        algo_type = AlgoType(body.algo_type.upper())
    except ValueError:
        raise HTTPException(400, detail=f"无效算法类型 '{body.algo_type}'（TWAP/VWAP/ICEBERG）")

    if body.side.upper() not in ("BUY", "SELL"):
        raise HTTPException(400, detail=f"无效方向 '{body.side}'（BUY/SELL）")
    if body.order_type.upper() not in ("MARKET", "LIMIT"):
        raise HTTPException(400, detail=f"无效订单类型 '{body.order_type}'")

    executor = get_algo_executor()
    try:
        algo = executor.submit_algo(
            symbol=body.symbol,
            market=body.market,
            side=body.side,
            total_qty=body.total_qty,
            algo_type=algo_type,
            order_type=body.order_type,
            limit_price=body.limit_price,
            strategy_id=body.strategy_id,
            duration_seconds=body.duration_seconds,
            slice_count=body.slice_count,
            display_qty=body.display_qty,
        )
    except AlgoValidationError as e:
        raise HTTPException(status_code=422, detail=f"算法参数校验失败: {e}")

    return AlgoOrderResponse(**algo.to_dict())


@router.get("/algo", response_model=list[AlgoOrderResponse])
async def list_algo_orders(
    strategy_id: Optional[str] = Query(None),
    algo_status: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
) -> list[AlgoOrderResponse]:
    """列出算法单。"""
    executor = get_algo_executor()
    algos = executor.list_algos(strategy_id=strategy_id, status=algo_status, limit=limit)
    return [AlgoOrderResponse(**a.to_dict()) for a in algos]


@router.get("/algo/{algo_id}", response_model=AlgoOrderResponse)
async def get_algo_order(algo_id: str) -> AlgoOrderResponse:
    """查询单个算法单进度。"""
    executor = get_algo_executor()
    algo = executor.get_algo(algo_id)
    if algo is None:
        raise HTTPException(status_code=404, detail=f"算法单 {algo_id} 不存在")
    return AlgoOrderResponse(**algo.to_dict())


@router.post("/algo/{algo_id}/cancel", response_model=AlgoOrderResponse)
async def cancel_algo_order(algo_id: str) -> AlgoOrderResponse:
    """撤销算法单（停止后续切片，已成交子单不回撤）。"""
    executor = get_algo_executor()
    try:
        algo = executor.cancel_algo(algo_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"算法单 {algo_id} 不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return AlgoOrderResponse(**algo.to_dict())
