"""
实盘对账 API（V3 · G6）

- POST /api/v1/reconcile/{market}   立即对账一次（只读）

**为什么除了 Celery 任务还要有这个端点**：`OrderManager` 的订单簿是进程内内存态，
Celery worker 里那份通常是空的，对出来的差异没有意义（详见
`app/tasks/reconcile.py` 模块 docstring）。这个端点跑在 FastAPI 进程内，
用的就是真正下过单的那个 OMS 实例 —— 它才是拿到可用对账结果的地方。

⚠️ **`position_diffs` 为空不代表对账通过。** 券商不可达时它同样为空。
前端请一律以 `is_clean` 判断，不要自己数 `position_diffs` 的长度。

只读：本端点不提交、不撤销、不修改任何订单，也不「自动纠正」差异。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.audit import AuditAction, audit_log
from app.core.rbac import Role, require_role
from app.data.models import Market
from app.oms.manager import get_order_manager
from app.oms.reconcile import SCOPE_NOTE, notify_reconcile, reconcile

router = APIRouter()


class PositionDiffOut(BaseModel):
    symbol: str
    local_qty: int
    broker_qty: int
    delta: int


class ReconcileResponse(BaseModel):
    market: str
    checked_at: str
    broker_reachable: bool
    is_clean: bool = Field(
        description="**唯一**可用于判断「账对上了」的字段：券商可达且零差异",
    )
    diff_count: int
    position_diffs: list[PositionDiffOut]
    cash_diff: float | None
    local_order_count: int
    broker_position_count: int
    checked_symbols: list[str]
    error: str
    scope: str
    scope_note: str = Field(SCOPE_NOTE, description="本地侧口径说明，请一并展示给用户")
    summary: str
    notified: bool


@router.post("/{market}", response_model=ReconcileResponse)
async def run_reconcile(
    market: Market,
    _user: Annotated[Any, Depends(require_role(Role.TRADER))],
    local_cash: Annotated[
        float | None,
        Body(embed=True, description="本地记账可用资金；不传则本次不对资金"),
    ] = None,
) -> ReconcileResponse:
    """
    立即对该市场执行一次只读对账，有差异或券商不可达时发 `RECONCILE_DIFF` 通知。

    未注册该市场的交易网关时返回 400 —— 刻意**不**返回一份「零差异」的成功响应，
    否则「没接券商」会被读成「对账通过」。
    """
    oms = get_order_manager()
    try:
        gateway = oms.get_gateway(market.value)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"未注册 {market.value} 交易网关，无法对账（这不等于对账通过）：{exc}",
        ) from exc

    result = await reconcile(market, oms, gateway, local_cash=local_cash)
    dispatch = notify_reconcile(result)

    await audit_log(
        AuditAction.RECONCILE_RUN,
        actor=getattr(_user, "email", "") or getattr(_user, "id", "system"),
        detail={
            "market": market.value,
            "broker_reachable": result.broker_reachable,
            "is_clean": result.is_clean,
            "diff_count": result.diff_count,
        },
    )

    return ReconcileResponse(
        **result.to_dict(),
        notified=not dispatch.get("skipped", False),
    )
