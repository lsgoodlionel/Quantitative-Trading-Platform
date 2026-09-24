"""L4 仓位调整循环（Wave L-c）

在**风险闸门之后、`on_bar` 之前**给策略一次机会追加或减少已有持仓（DCA、分批止盈）。

> **许可证**：`adjust_position` 的**语义**设计参考自 freqtrade（GPL-3.0）的
> `IStrategy.adjust_trade_position`。此处仅阅读其设计思路后**独立实现**，
> 未复制其任何代码。

三个必须做对的点：

1. **已经该止损的仓位不再被加仓**。风险闸门先跑，命中即挂平仓单；这里看到该标的
   有在途风险平仓单就整个跳过。
2. **加仓走 K-d 已有的持仓更新路径**（`_track_trade` → `next_trade`），由它统一
   把开仓价对齐到新的平均成本并重置收益率极值。若在这里另写一份，追踪止损会在
   毫无回撤的情况下把刚加的仓打掉。
3. **调整次数上限真的生效**。超限记 WARNING 后忽略，绝不静默丢弃。

这是**混入类**，依赖宿主提供：`_open_trades` / `_has_pending_risk_exit()` /
`buy()` / `sell()` / `short()` / `cover()`。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from app.engine.backtest.broker import Order
    from app.engine.backtest.trade import Trade
    from app.strategy.base import StrategyBase
    from app.strategy.context import PortfolioContext, StrategyContext

logger = logging.getLogger(__name__)

#: 仓位调整产生的平仓 Fill 的退出原因（直接进 tag_metrics.py 的分组统计）
EXIT_PARTIAL = "partial_exit"


class _AdjustCount(NamedTuple):
    """某标的当前这**一笔**交易已发生的调整次数。

    带上 (开仓时间, 方向) 是为了识别「换了一笔交易」：`next_trade` 在方向翻转或
    清仓后重开时会造出一个新的 `Trade`（新的 open_time），此时计数必须归零，
    否则上一笔用满 10 次会连累下一笔一次都调不了。
    """

    open_time: datetime
    direction: str
    count: int


class PositionAdjustMixin:
    """给券商加上 L4 的仓位调整循环。"""

    def _init_adjust_state(self) -> None:
        """由宿主的 `__init__` 调用。"""
        self._adjust_counts: dict[str, _AdjustCount] = {}
        #: 已就「调整次数超限」告警过的标的，避免逐 bar 刷同样的日志
        self._reported_adjust_limits: set[str] = set()

    def adjust_positions(
        self,
        strategy: StrategyBase,
        ctx: StrategyContext | PortfolioContext | None,
        prices: Mapping[str, float],
        now: datetime,
    ) -> list[Order]:
        """
        逐个有仓标的询问 `adjust_position`，把返回的增量金额翻译成委托单。

        必须在 `check_exit_conditions` **之后**、`on_bar/on_bars` **之前**调用。
        `position_adjustment_enable=False` 时立即返回，零开销。
        """
        if not getattr(strategy, "position_adjustment_enable", False):
            return []

        orders: list[Order] = []
        for symbol, trade in list(self._open_trades.items()):
            price = prices.get(symbol)
            if price is None or price <= 0:
                continue
            if self._has_pending_risk_exit(symbol):
                continue        # 该止损的仓位不该再被加仓，也不必再叠一张平仓单
            order = self._adjust_one(strategy, ctx, symbol, trade, price)
            if order is not None:
                orders.append(order)
        return orders

    def _adjust_one(
        self,
        strategy: StrategyBase,
        ctx: StrategyContext | PortfolioContext | None,
        symbol: str,
        trade: Trade,
        price: float,
    ) -> Order | None:
        """单个标的的调整判定；产出委托单并记账调整次数。"""
        delta_value = strategy.adjust_position(ctx, trade, trade.current_profit(price))
        if delta_value is None or delta_value == 0:
            return None
        if not self._consume_adjust_quota(strategy, symbol, trade, delta_value):
            return None

        order = (
            self._submit_increase(symbol, trade, delta_value, price)
            if delta_value > 0
            else self._submit_reduce(symbol, trade, -delta_value, price)
        )
        if order is None:
            return None
        self._commit_adjust(symbol, trade, order)
        return order

    # ── 次数上限 ─────────────────────────────────────────────

    def _consume_adjust_quota(
        self, strategy: StrategyBase, symbol: str, trade: Trade, delta_value: float
    ) -> bool:
        """本次调整是否还在配额内。超限则记 WARNING 并返回 False（绝不静默忽略）。"""
        limit = getattr(strategy, "max_position_adjustments", 0)
        used = self._used_adjustments(symbol, trade)
        if used < limit:
            self._reported_adjust_limits.discard(symbol)
            return True

        level = logging.DEBUG if symbol in self._reported_adjust_limits else logging.WARNING
        self._reported_adjust_limits.add(symbol)
        logger.log(
            level,
            "%s 本笔交易已调整 %d 次，达到 max_position_adjustments=%d，"
            "本次 %.2f 的调整被忽略",
            symbol,
            used,
            limit,
            delta_value,
        )
        return False

    def _used_adjustments(self, symbol: str, trade: Trade) -> int:
        """本笔交易已用掉的调整次数（换了一笔交易即视为 0）。"""
        record = self._adjust_counts.get(symbol)
        if record is None or not _same_trade(record, trade):
            return 0
        return record.count

    def _commit_adjust(self, symbol: str, trade: Trade, order: Order) -> None:
        """委托成功入队才计数 —— 被券商拒掉的单子不该吃掉配额。"""
        from app.engine.backtest.broker import OrderStatus

        if order.status is not OrderStatus.PENDING:
            logger.debug("%s 的仓位调整单未入队(%s)，不计入配额", symbol, order.status.value)
            return
        self._adjust_counts[symbol] = _AdjustCount(
            open_time=trade.open_time,
            direction=trade.direction,
            count=self._used_adjustments(symbol, trade) + 1,
        )

    # ── 委托构造 ─────────────────────────────────────────────

    def _submit_increase(
        self, symbol: str, trade: Trade, value: float, price: float
    ) -> Order | None:
        """加仓：多头买入、空头继续做空。成交后由 `_track_trade` 统一重置本笔基准。"""
        qty = int(value / price)
        if qty <= 0:
            logger.debug("%s 的加仓金额 %.2f 不足一股（价 %.4f），本次跳过", symbol, value, price)
            return None
        if trade.direction == "long":
            return self.buy(symbol, qty, entry_tag=trade.entry_tag)
        return self.short(symbol, qty, entry_tag=trade.entry_tag)

    def _submit_reduce(
        self, symbol: str, trade: Trade, value: float, price: float
    ) -> Order | None:
        """
        部分平仓：绝对值 >= 当前持仓市值时全平，绝不卖穿成反向持仓。

        产出的 Fill 带 `exit_reason="partial_exit"`，直接进回合分析与标签统计。
        """
        qty = min(int(value / price), trade.qty)
        if value >= trade.qty * price:
            qty = trade.qty                     # 覆盖整个仓位市值 → 全平
        if qty <= 0:
            logger.debug("%s 的减仓金额 %.2f 不足一股（价 %.4f），本次跳过", symbol, value, price)
            return None
        if trade.direction == "long":
            return self.sell(symbol, qty, exit_reason=EXIT_PARTIAL)
        return self.cover(symbol, qty, exit_reason=EXIT_PARTIAL)


def _same_trade(record: _AdjustCount, trade: Trade) -> bool:
    return record.open_time == trade.open_time and record.direction == trade.direction


__all__ = ["EXIT_PARTIAL", "PositionAdjustMixin"]
