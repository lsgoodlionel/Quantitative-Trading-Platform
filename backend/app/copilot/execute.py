"""草稿执行（V3 Wave B-c / I1）—— 走既有端点，不另开通路

用户点「确认执行」后到这里。**这里没有任何一行下单逻辑**：

- 订单   → `app.api.v1.endpoints.orders.submit_order`（同一套风控、同一套 OMS）
- 再平衡 → `app.api.v1.endpoints.rebalance.execute_rebalance`（同一套 confirm_token 校验）

参数在这里**重新用同一个 Pydantic 模型校验一遍**：草稿由前端回传，不能当可信输入。
`draft_id` 是一次性幂等标识 —— 先校验、后占用、再执行：参数不合法不消耗草稿，
一旦真正下发就不允许重放（连点两下不会买两次）。执行本身失败也不释放占用：
宁可让用户重新生成一张草稿，也不冒重复下单的风险。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from app.api.v1.endpoints import orders as orders_endpoint
from app.api.v1.endpoints import rebalance as rebalance_endpoint
from app.api.v1.endpoints.auth import UserInfo
from app.copilot.args import SubmitOrderRequest
from app.copilot.drafts import ORDER_ACTION, REBALANCE_ACTION

#: 已执行草稿 ID 的记忆容量。会话本期不持久化，这里同样是进程内的。
MAX_REMEMBERED_DRAFTS = 2000

_consumed_ids: deque[str] = deque(maxlen=MAX_REMEMBERED_DRAFTS)
_consumed_lookup: set[str] = set()


class DraftExecutionError(Exception):
    """草稿执行失败，带上要回给前端的 HTTP 状态码。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def reset_consumed_drafts() -> None:
    """清空幂等记录（仅供测试使用）。"""
    _consumed_ids.clear()
    _consumed_lookup.clear()


# ── 各动作对应的「既有端点」 ──────────────────────────────────────────────────

async def _run_order(body: BaseModel, user: UserInfo) -> dict[str, Any]:
    order = await orders_endpoint.submit_order(body, orders_endpoint.get_oms(), user)
    return {"action": ORDER_ACTION, "result": order.model_dump()}


async def _run_rebalance(body: BaseModel, user: UserInfo) -> dict[str, Any]:
    outcome = await rebalance_endpoint.execute_rebalance(
        body, orders_endpoint.get_oms(), user
    )
    return {"action": REBALANCE_ACTION, "result": outcome.model_dump()}


Runner = Callable[[BaseModel, UserInfo], Awaitable[dict[str, Any]]]

_ACTIONS: dict[str, tuple[type[BaseModel], Runner]] = {
    ORDER_ACTION: (SubmitOrderRequest, _run_order),
    REBALANCE_ACTION: (rebalance_endpoint.RebalanceExecuteRequest, _run_rebalance),
}


async def execute_draft(
    *, draft_id: str, action: str, params: dict[str, Any], user: UserInfo
) -> dict[str, Any]:
    """执行一张草稿。参数会被重新校验，执行一律走既有端点。"""
    entry = _ACTIONS.get(action)
    if entry is None:
        raise DraftExecutionError(400, f"未知的草稿动作：{action}")

    model, runner = entry
    body = _validate(model, params)
    _claim(draft_id)
    try:
        return await runner(body, user)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        raise DraftExecutionError(exc.status_code, detail) from exc


# ── 内部 ─────────────────────────────────────────────────────────────────────

def _claim(draft_id: str) -> None:
    """占用一个草稿 ID；已被占用则拒绝重放。"""
    if draft_id in _consumed_lookup:
        raise DraftExecutionError(409, "该草稿已经执行过了，请重新生成一张。")
    if len(_consumed_ids) == _consumed_ids.maxlen:
        _consumed_lookup.discard(_consumed_ids[0])
    _consumed_ids.append(draft_id)
    _consumed_lookup.add(draft_id)


def _validate(model: type[BaseModel], params: dict[str, Any]) -> BaseModel:
    """草稿来自前端，执行前必须重新校验。"""
    try:
        return model.model_validate(params)
    except Exception as exc:  # noqa: BLE001 —— Pydantic ValidationError 也在内
        raise DraftExecutionError(422, f"草稿参数不合法：{exc}") from exc
