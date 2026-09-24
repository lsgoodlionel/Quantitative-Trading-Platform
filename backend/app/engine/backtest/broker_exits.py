"""K4 风险闸门：本笔交易跟踪 + 止损/ROI/追踪止损（Wave K-d）

从 `broker.py` 拆出来的一块 —— 撮合与风险闸门是两件事，混在一个 800 行的
`SimulatedBroker` 里只会越滚越大。这里是**混入类**，依赖宿主提供：
`_positions` / `_pending` / `sell()` / `cover()`。

规则语义定义在 `trade.py`（设计参考自 freqtrade，独立实现）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING

from app.engine.backtest.trade import (
    RISK_EXIT_REASONS,
    ExitRules,
    Trade,
    evaluate_exit,
    next_trade,
)

if TYPE_CHECKING:
    from app.engine.backtest.broker import Fill, Order
    from app.strategy.base import StrategyBase
    from app.strategy.context import PortfolioContext, StrategyContext

logger = logging.getLogger(__name__)


class RiskExitMixin:
    """给券商加上「本笔交易」概念与风险闸门。"""

    def _init_risk_state(self) -> None:
        """由宿主的 `__init__` 调用。"""
        #: 每个有仓标的的本笔交易（Position 管成本，Trade 管本笔状态）
        self._open_trades: dict[str, Trade] = {}
        #: 已通过自检的退出配置（配置不变时跳过重复校验）
        self._validated_rules: ExitRules | None = None
        #: 已就「平仓单被拒」告警过的标的，避免 T+1 期间每根 bar 刷同样的日志
        self._reported_exit_rejects: set[str] = set()

    @property
    def open_trades(self) -> dict[str, Trade]:
        """当前每个有仓标的的本笔交易（只读快照）。"""
        return dict(self._open_trades)

    # ── 本笔交易跟踪 ─────────────────────────────────────────

    def _track_trade(self, fill: Fill) -> None:
        """每笔成交后同步该标的的 `Trade`（纯记账，不改变任何撮合结果）。"""
        position = self._positions.get(fill.symbol)
        updated = next_trade(
            self._open_trades.get(fill.symbol),
            symbol=fill.symbol,
            net_qty=position.qty,
            avg_cost=position.avg_cost,
            filled_at=fill.filled_at,
            entry_tag=fill.entry_tag,
        )
        if updated is None:
            self._open_trades.pop(fill.symbol, None)
        else:
            self._open_trades[fill.symbol] = updated

    # ── 风险闸门 ─────────────────────────────────────────────

    def check_exit_conditions(
        self,
        strategy: StrategyBase,
        ctx: StrategyContext | PortfolioContext | None,
        prices: Mapping[str, float],
        now: datetime,
    ) -> list[Order]:
        """
        ROI → 止损 → 追踪止损，命中即挂平仓单（下一根 bar 成交）。

        必须在 `strategy.on_bar/on_bars` **之前**调用 —— 否则策略会在一个
        本该止损的仓位上继续加仓。三项配置全为默认时立即返回，零开销。
        """
        rules = strategy.exit_rules()
        if not rules.is_enabled:
            return []
        if rules != self._validated_rules:
            rules.validate()          # 配置不变就不必每根 bar 重复自检
            self._validated_rules = rules

        orders: list[Order] = []
        for symbol, trade in list(self._open_trades.items()):
            price = prices.get(symbol)
            if price is None or self._has_pending_risk_exit(symbol):
                continue
            order = self._exit_order_for(strategy, rules, ctx, symbol, trade, price, now)
            if order is not None:
                orders.append(order)
        return orders

    def _exit_order_for(
        self,
        strategy: StrategyBase,
        rules: ExitRules,
        ctx: StrategyContext | PortfolioContext | None,
        symbol: str,
        trade: Trade,
        price: float,
        now: datetime,
    ) -> Order | None:
        """单个标的的退出判定；命中则挂出平仓单。"""
        profit = trade.current_profit(price)
        marked = trade.mark_profit(profit)
        self._open_trades[symbol] = marked

        reason = evaluate_exit(
            rules,
            marked,
            profit,
            marked.duration_minutes(now),
            custom_stoploss=strategy.custom_stoploss(ctx, marked, profit),
            custom_roi=strategy.custom_roi(ctx, marked, profit),
        )
        if reason is None:
            return None

        if marked.direction == "long":
            order = self.sell(symbol, marked.qty, exit_reason=reason)
        else:
            order = self.cover(symbol, marked.qty, exit_reason=reason)
        self._log_exit_rejection(order, symbol, reason)
        return order

    def _has_pending_risk_exit(self, symbol: str) -> bool:
        """该标的是否已有在途的风险平仓单（避免每根 bar 重复挂单）。"""
        return any(
            o.symbol == symbol and o.exit_reason in RISK_EXIT_REASONS
            for o in self._pending
        )

    def _log_exit_rejection(self, order: Order, symbol: str, reason: str) -> None:
        """平仓单被拒（如 A股 T+1）会每根 bar 重试，日志只在首次用 WARNING。"""
        # 局部 import：broker 反过来要 import 本模块的混入类，模块级会成环
        from app.engine.backtest.broker import OrderStatus

        if order.status is not OrderStatus.REJECTED:
            self._reported_exit_rejects.discard(symbol)
            return
        level = (
            logging.DEBUG if symbol in self._reported_exit_rejects else logging.WARNING
        )
        self._reported_exit_rejects.add(symbol)
        logger.log(level, "%s 的 %s 平仓单被拒: %s", symbol, reason, order.reject_reason)


__all__ = ["RiskExitMixin"]
