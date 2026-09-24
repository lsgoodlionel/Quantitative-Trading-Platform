"""L5 下单确认与价格自定义钩子（Wave L-c）

在订单进入挂单队列**之前**插一道策略确认：可以否决、可以改价、可以设分钟级超时。

> **许可证**：`confirm_entry` / `confirm_exit` / `custom_*_price` 的**语义**
> 设计参考自 freqtrade（GPL-3.0）的 `IStrategy`。此处仅阅读其设计思路后
> **独立实现**，未复制其任何代码。

两条不能含糊的设计约束：

1. **`custom_*_price` 必须产出 LIMIT 单**。策略说「我要在 98.5 成交」，若引擎
   直接按 98.5 市价撮合，那是拿着当根 bar 的信息挑了个自己喜欢的价 —— 偷看未来。
   正确做法是转成限价挂单，成不成交由后续 bar 的 OHLC 决定。
2. **否决风险闸门平仓要留痕**。止损被策略否决后仓位会一直留着，必须有 WARNING，
   不能悄无声息。

这是**混入类**，依赖宿主提供：`_positions` / `_last_bar_time`。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from app.core.errors import StrategyContractError
from app.engine.backtest.order_types import LIMIT_ORDER_TYPES, OrderType
from app.engine.backtest.trade import RISK_EXIT_REASONS

if TYPE_CHECKING:
    from app.engine.backtest.broker import Order
    from app.strategy.base import StrategyBase
    from app.strategy.context import PortfolioContext, StrategyContext

logger = logging.getLogger(__name__)

#: 引擎尚未推进任何 bar 时（如 `on_start` 里下单），钩子拿到的参考价
UNKNOWN_PRICE = 0.0

#: 未显式标注退出原因的平仓单，交给钩子时统一记这个（与 broker 落账口径一致）
DEFAULT_EXIT_REASON = "signal"

#: 可以安全地整体转成 LIMIT 的订单类型（触发类订单转限价会破坏其触发语义）
_MARKET_LIKE_TYPES = frozenset(
    {OrderType.MARKET, OrderType.MARKET_ON_OPEN, OrderType.MARKET_ON_CLOSE}
)


@dataclass(frozen=True)
class OrderHookBinding:
    """一次时点推进期间，券商该向谁询问下单确认。"""

    strategy: StrategyBase
    ctx: StrategyContext | PortfolioContext | None


class OrderHookMixin:
    """给券商加上 L5 的下单确认 / 改价 / 超时能力。"""

    def _init_order_hook_state(self) -> None:
        """由宿主的 `__init__` 调用。"""
        #: 当前绑定的策略钩子。None = 策略一个钩子都没覆盖，整条路径不进入
        self._order_hooks: OrderHookBinding | None = None
        #: 各标的最近一次撮合所见的收盘价，作为钩子的 `proposed` 参考价
        self._hook_prices: dict[str, float] = {}
        #: 已就「风险平仓被否决」告警过的 (标的, 原因)，避免逐 bar 刷同样的日志
        self._reported_exit_vetoes: set[tuple[str, str]] = set()

    def bind_order_hooks(
        self,
        strategy: StrategyBase | None,
        ctx: StrategyContext | PortfolioContext | None,
    ) -> None:
        """绑定/解绑本时点的下单钩子。传 None 解绑（回到零开销路径）。"""
        self._order_hooks = None if strategy is None else OrderHookBinding(strategy, ctx)

    def hook_price(self, symbol: str) -> float:
        """钩子看到的参考价：最近一次已知收盘价，从未见过行情时为 `UNKNOWN_PRICE`。"""
        return self._hook_prices.get(symbol, UNKNOWN_PRICE)

    def _record_hook_price(self, symbol: str, close: float) -> None:
        """由宿主在撮合每根 bar 时调用（撮合已发生，用其收盘价不构成偷看）。"""
        self._hook_prices[symbol] = close

    # ── 下单前的确认 / 改价 / 超时 ───────────────────────────

    def apply_order_hooks(self, order: Order) -> bool:
        """
        在订单入队前跑一遍 L5 钩子。返回 False 表示**被策略否决**（订单已置为
        CANCELLED，调用方直接把它返回给策略即可）。

        未绑定任何钩子时直接放行（调用方通常已在热路径上先判过一次）。
        """
        binding = self._order_hooks
        if binding is None:
            return True
        strategy, ctx = binding.strategy, binding.ctx
        is_exit = self._is_exit_order(order)
        proposed = self.hook_price(order.symbol)

        if is_exit:
            reason = order.exit_reason or DEFAULT_EXIT_REASON
            if not strategy.confirm_exit(ctx, order.symbol, order.qty, proposed, reason):
                self._veto(order, f"策略 confirm_exit 否决平仓（{reason}）")
                self._log_exit_veto(order.symbol, reason)
                return False
            custom = strategy.custom_exit_price(ctx, order.symbol, proposed, reason)
        else:
            if not strategy.confirm_entry(
                ctx, order.symbol, order.qty, proposed, order.entry_tag
            ):
                self._veto(order, "策略 confirm_entry 否决开仓")
                return False
            custom = strategy.custom_entry_price(ctx, order.symbol, proposed)

        _apply_custom_price(order, custom)
        self._apply_timeout(order, strategy, is_exit)
        return True

    def _is_exit_order(self, order: Order) -> bool:
        """
        这张单是平仓还是开仓。

        判据是「它是否**减少**当前净持仓」：净持仓为正时的 SELL、为负时的 BUY 都是
        平仓。已显式带 `exit_reason` 的（风险闸门 / 部分平仓）直接算平仓。
        """
        from app.engine.backtest.broker import OrderSide

        if order.exit_reason is not None:
            return True
        held = self._positions.get(order.symbol).qty
        if held > 0:
            return order.side is OrderSide.SELL
        if held < 0:
            return order.side is OrderSide.BUY
        return False

    @staticmethod
    def _veto(order: Order, reason: str) -> None:
        """被策略否决的订单不入队，状态记 CANCELLED（它没被券商拒，是策略撤的）。"""
        from app.engine.backtest.broker import OrderStatus

        order.status = OrderStatus.CANCELLED
        order.reject_reason = reason

    def _log_exit_veto(self, symbol: str, reason: str) -> None:
        """
        否决风险闸门平仓 = 止损失效，必须留痕。

        风险闸门每根 bar 都会重试，所以同一 (标的, 原因) 只在首次用 WARNING，
        之后降为 DEBUG —— 既不刷屏，也不会让第一次的告警被淹没。
        """
        if reason not in RISK_EXIT_REASONS:
            return
        key = (symbol, reason)
        level = logging.DEBUG if key in self._reported_exit_vetoes else logging.WARNING
        self._reported_exit_vetoes.add(key)
        logger.log(
            level,
            "%s 的 %s 平仓被 confirm_exit 否决，仓位将继续持有 —— 风险闸门本次未生效",
            symbol,
            reason,
        )

    def _apply_timeout(self, order: Order, strategy: StrategyBase, is_exit: bool) -> None:
        """
        给订单打上分钟级超时。与 TimeInForce 是**并存**关系：两者都会被检查，
        先到者生效（= 取更早者）。
        """
        minutes = (
            strategy.exit_timeout_minutes if is_exit else strategy.entry_timeout_minutes
        )
        if minutes is None:
            return
        if minutes <= 0:
            field = "exit_timeout_minutes" if is_exit else "entry_timeout_minutes"
            raise StrategyContractError(f"{field} 必须为正整数或 None，收到 {minutes}")
        if self._last_bar_time is None:
            return          # 还没有时间锚点（首根 bar 之前下的单），本次不设超时
        order._expire_at = self._last_bar_time + timedelta(minutes=minutes)


def _apply_custom_price(order: Order, price: float | None) -> None:
    """把策略自定义的价格落到订单上 —— 一律转成 LIMIT，绝不当成市价成交价。"""
    if price is None:
        return
    if price <= 0:
        raise StrategyContractError(
            f"custom_entry_price / custom_exit_price 必须返回正数或 None，收到 {price}"
        )

    if order.order_type in _MARKET_LIKE_TYPES:
        order.order_type = OrderType.LIMIT
        order.limit_price = price
        return
    if order.order_type in LIMIT_ORDER_TYPES:
        order.limit_price = price      # 已经是限价类，只改价不改类型
        return

    # STOP_MARKET / TRAILING_STOP：转成限价会把「触发后市价出场」变成「挂着不一定成交」，
    # 那是把止损单悄悄变成可能不成交的单子。宁可忽略并告警。
    logger.warning(
        "%s 是 %s 订单，自定义价格 %.4f 被忽略：转成限价会破坏其触发语义",
        order.symbol,
        order.order_type.value,
        price,
    )


__all__ = ["UNKNOWN_PRICE", "OrderHookBinding", "OrderHookMixin"]
