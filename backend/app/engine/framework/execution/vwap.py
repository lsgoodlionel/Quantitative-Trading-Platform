"""按成交量占比拆单的执行模型（Wave L-b / L3）

语义对齐 Lean `Algorithm.Framework/Execution/VolumeWeightedAveragePriceExecutionModel`
（Apache-2.0）；本项目 `refs/` 下没有 Lean 源码副本，按契约描述独立实现。

**与 K6 撮合层成交量约束的区别**（契约 §3.1，容易混淆）：

- `VolumeShareSlippage.fill_limit()` 是**撮合层**约束 —— 券商侧「这根 bar 最多
  成交这么多」，策略无法规避；
- 本模型是**执行层**策略 —— 策略主动把大单拆小以降低冲击成本。

两者叠加是正常的：执行模型先拆，撮合层再对拆出来的单子施加上限。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.engine.backtest.broker import Order, OrderSide, OrderStatus
from app.engine.framework.execution.base import ExecutionModel, build_plan, to_order
from app.engine.framework.target import PortfolioTarget

if TYPE_CHECKING:
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)


#: 仍可能成交的订单状态 —— 这些订单的未成交部分属于「在途」
_LIVE_STATUS = (OrderStatus.PENDING, OrderStatus.PARTIAL)


class VolumeWeightedAveragePriceExecution(ExecutionModel):
    """
    单根 bar 的委托量不超过 `max_order_percent_volume × bar.volume`，
    未完成的部分留到下一次 `execute()` 继续推进。

    残量不需要单独记账：模型缓存的是**目标持仓**而非增量，每次 `execute()`
    都重新算 diff，目标达成后该标的从工作集里移除。

    ⚠️ diff 必须扣掉**在途委托**，而不能只看 `ctx.qty()`：拆出来的单子未必在
    下一根 bar 就全部成交（撮合层的成交量上限会把它截断并重新挂回队列）。
    只看已成交持仓会在每根 bar 重复补一次同样的量，最终大幅超买/超卖。
    在途量由模型跟踪自己产出的订单得出 —— 本模型假定调用方会把返回的订单
    交给 `ctx.submit()`（`FrameworkStrategy` 即如此），未提交的订单会被
    当成永远在途。

    工作集里的目标在达成之前一直有效，即使后续调仓没有再提到该标的 ——
    这正是「残量顺延」的含义。要放弃一个未完成的目标，显式下发同标的的
    新目标即可覆盖（清仓就下 `quantity=0`）。
    """

    name = "vwap"

    def __init__(self, max_order_percent_volume: float = 0.01) -> None:
        if not 0.0 < max_order_percent_volume <= 1.0:
            raise ValueError(
                f"max_order_percent_volume 必须落在 (0, 1] 区间内，"
                f"收到 {max_order_percent_volume}"
            )
        self.max_order_percent_volume = max_order_percent_volume
        #: symbol → 尚未达成的目标持仓
        self._working: dict[str, PortfolioTarget] = {}
        #: symbol → 本模型产出且仍可能成交的订单
        self._inflight: dict[str, list[Order]] = {}

    @property
    def working_targets(self) -> dict[str, PortfolioTarget]:
        """尚未完成的目标（只读快照，便于测试与监控）。"""
        return dict(self._working)

    def execute(
        self, ctx: PortfolioContext, targets: list[PortfolioTarget]
    ) -> list[Order]:
        for target in targets:            # 新指令覆盖同标的的旧指令，不叠加
            self._working[target.symbol] = target
        self._prune_inflight()
        self._retire(ctx)

        plan = build_plan(
            ctx, list(self._working.values()), position_of=self._effective_position(ctx)
        )
        orders: list[Order] = []
        for target, diff in plan:
            sliced = self._slice(ctx, target.symbol, diff)
            if sliced != 0:
                orders.append(to_order(ctx, target, sliced))
        return self._register(orders)

    # ── 在途委托 ─────────────────────────────────────────────

    def _register(self, orders: list[Order]) -> list[Order]:
        for order in orders:
            self._inflight.setdefault(order.symbol, []).append(order)
        return orders

    def _prune_inflight(self) -> None:
        """成交完 / 被拒 / 被撤的订单不再算作在途。"""
        for symbol in list(self._inflight):
            live = [o for o in self._inflight[symbol] if o.status in _LIVE_STATUS]
            if live:
                self._inflight[symbol] = live
            else:
                del self._inflight[symbol]

    def _inflight_qty(self, symbol: str) -> int:
        """
        该标的在途委托的带符号未成交量（BUY 为正，SELL 为负）。

        取 `order.qty` 而不是 `qty - filled_qty`：撮合层部分成交后会把
        `order.qty` **就地改写成残量**再挂回队列（`broker.py` 的 PARTIAL 分支），
        而 `filled_qty` 是累计值 —— 两者相减会把已成交部分重复扣一次。
        """
        total = 0
        for order in self._inflight.get(symbol, ()):
            total += order.qty if order.side is OrderSide.BUY else -order.qty
        return total

    def _effective_position(self, ctx: PortfolioContext) -> Callable[[str], int]:
        """给 `build_plan` 用的持仓口径：已成交持仓 + 在途委托。"""
        return lambda symbol: ctx.qty(symbol) + self._inflight_qty(symbol)

    def _retire(self, ctx: PortfolioContext) -> None:
        """已经达成的目标不再占用工作集。"""
        done = [
            s
            for s, t in self._working.items()
            if t.quantity == ctx.qty(s) and self._inflight_qty(s) == 0
        ]
        for symbol in done:
            del self._working[symbol]

    def _slice(self, ctx: PortfolioContext, symbol: str, diff: int) -> int:
        """本根 bar 允许下的量（带符号）；无法估算成交量时返回 0（本 bar 不下单）。"""
        bar = ctx.bar(symbol)
        if bar is None:
            logger.debug("%s 本时点无 bar，无法估算成交量，本 bar 不拆单", symbol)
            return 0
        limit = int(bar.volume * self.max_order_percent_volume)
        if limit <= 0:
            logger.debug("%s 成交量 %.0f 太小，占比取整后为 0，本 bar 不拆单", symbol, bar.volume)
            return 0
        capped = min(abs(diff), limit)
        return capped if diff > 0 else -capped


__all__ = ["VolumeWeightedAveragePriceExecution"]
