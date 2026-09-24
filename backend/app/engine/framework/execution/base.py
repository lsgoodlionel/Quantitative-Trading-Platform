"""ExecutionModel 抽象 + 立即执行实现 + 各执行模型共用的计划构建（K-d / L-b）

接口对齐 Lean 的 `IExecutionModel`（Apache-2.0）。

**核心语义**：`PortfolioTarget.quantity` 是目标持仓，执行模型产出的是**增量 diff**。
已有 100 股、目标 150 股 → 买 50 股，而不是买 150 股。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.engine.backtest.broker import Order, OrderSide
from app.engine.framework.target import PortfolioTarget

if TYPE_CHECKING:
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)

#: 执行计划的一项：(目标, 需要成交的增量数量，带符号)
ExecutionLeg = tuple[PortfolioTarget, int]


class ExecutionModel(ABC):
    """目标持仓 → 订单。返回的订单**尚未提交**，由调用方 `ctx.submit()`。"""

    name: str = "execution"

    @abstractmethod
    def execute(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[Order]:
        """产出把当前持仓推向目标持仓所需的订单。"""


def build_plan(
    ctx: PortfolioContext,
    targets: list[PortfolioTarget],
    *,
    position_of: Callable[[str], int] | None = None,
) -> list[ExecutionLeg]:
    """
    目标持仓 → `(目标, 增量 diff)` 计划，全部执行模型共用的前置处理：

    1. 无价标的（停牌 / 未上市）下不了单，跳过；
    2. 被跳过的标的若属于某个分组，**整组放弃** —— 否则配对交易会只剩一条腿裸露；
    3. diff 为 0 的目标不产生订单；
    4. 排序固定为**先卖后买**，让释放出来的现金能被同一时点的买单用上。

    `position_of` 用于替换「当前持仓」的口径，缺省是 `ctx.qty`。分多根 bar
    推进的执行模型需要把**在途委托**一并算进来，否则会重复下单。
    """
    held = position_of or ctx.qty
    plan: list[ExecutionLeg] = []
    blocked: set[str] = set()

    for target in targets:
        price = ctx.price(target.symbol)
        if price is None or price <= 0:
            if target.group_id:
                blocked.add(target.group_id)
            logger.debug("%s 无可用价格，跳过执行", target.symbol)
            continue
        diff = target.quantity - held(target.symbol)
        if diff != 0:
            plan.append((target, diff))

    if blocked:
        logger.warning("分组 %s 有腿不可交易，整组放弃执行", sorted(blocked))
        plan = [(t, d) for t, d in plan if t.group_id not in blocked]

    plan.sort(key=lambda item: (item[1] > 0, item[0].symbol))
    return plan


def to_order(ctx: PortfolioContext, target: PortfolioTarget, diff: int) -> Order:
    """把一条增量 diff 变成市价单。"""
    held = ctx.qty(target.symbol)
    # 委托方向与当前持仓方向相反 = 在减仓/平仓，标记 exit_reason 而非 entry_tag
    is_reducing = held != 0 and (diff > 0) != (held > 0)
    return Order(
        symbol=target.symbol,
        market=ctx.market_of(target.symbol),
        side=OrderSide.BUY if diff > 0 else OrderSide.SELL,
        qty=abs(diff),
        entry_tag=None if is_reducing else (target.tag or None),
        exit_reason=(target.tag or "rebalance") if is_reducing else None,
    )


class ImmediateExecutionModel(ExecutionModel):
    """立即以市价单补齐差额。"""

    name = "immediate"

    def execute(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[Order]:
        return [to_order(ctx, target, diff) for target, diff in build_plan(ctx, targets)]


__all__ = [
    "ExecutionLeg",
    "ExecutionModel",
    "ImmediateExecutionModel",
    "build_plan",
    "to_order",
]
