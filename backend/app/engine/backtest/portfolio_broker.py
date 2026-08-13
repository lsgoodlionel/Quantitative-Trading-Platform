"""多标的撮合与账户（Wave K-c / K1）

在 `SimulatedBroker` 之上补三件事，其余撮合语义（订单类型、部分成交、T+1、
涨跌停、融券费）全部原样复用，保证单标的路径逐笔不变：

1. **一个时点撮合多个标的**：`process_bars()` 在一个时点内按标的逐一撮合，
   融券费计提与 TIF 过期各只做一次（而不是每标的一次）。
2. **最后已知价**：停牌/未上市的标的没有 bar，估值必须沿用最近一次收盘价。
   用 0 或成本价都会让组合净值出现假暴跌 / 假平滑（契约 §3.2）。
3. **同时持仓上限**：`max_open_positions` 在下单时即拒绝新开标的，
   而不是等到成交后再回滚。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

from app.data.models import Bar, Market
from app.engine.backtest.broker import (
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    SimulatedBroker,
)
from app.engine.backtest.commission import CommissionModel
from app.engine.backtest.slippage import SlippageModel
from app.engine.controls.base import AccountControl, TradingControl

logger = logging.getLogger(__name__)


class PortfolioBroker(SimulatedBroker):
    """组合券商：同一账户下多标的共享现金与持仓上限。"""

    def __init__(
        self,
        initial_cash: float,
        market: Market,
        commission_model: CommissionModel | None = None,
        slippage_model: SlippageModel | None = None,
        allow_short: bool = False,
        short_borrow_rate: float = 0.0,
        max_open_positions: int | None = None,
        controls: Sequence[TradingControl] | None = None,
        account_controls: Sequence[AccountControl] | None = None,
    ) -> None:
        if max_open_positions is not None and max_open_positions <= 0:
            raise ValueError("max_open_positions 必须为正整数或 None")

        super().__init__(
            initial_cash=initial_cash,
            market=market,
            commission_model=commission_model,
            slippage_model=slippage_model,
            allow_short=allow_short,
            short_borrow_rate=short_borrow_rate,
            controls=controls,
            account_controls=account_controls,
        )
        self._max_open_positions = max_open_positions

    # ── 最后已知价 ───────────────────────────────────────────

    @property
    def max_open_positions(self) -> int | None:
        return self._max_open_positions

    def last_known_price(self, symbol: str) -> float | None:
        """最近一次见到的收盘价；从未有过 bar 时返回 None。"""
        return self._last_prices.get(symbol)

    def mark_prices(self) -> dict[str, float]:
        """全体标的的估值价快照（停牌标的沿用最后已知价）。"""
        return dict(self._last_prices)

    def portfolio_value(self, prices: Mapping[str, float] | None = None) -> float:
        """未显式给价时，用最后已知价估值 —— 停牌标的不会被当成 0。"""
        marks = self._last_prices if prices is None else dict(prices)
        return self.cash + self.positions.total_market_value(marks)

    # ── 撮合 ─────────────────────────────────────────────────

    def process_bars(self, ts: datetime, bars: Mapping[str, Bar]) -> list[Fill]:
        """
        撮合一个时点上所有有 bar 的标的，返回本时点产生的全部 Fill。

        与逐标的调用 `process_bar()` 的差别（也是必须单独实现的原因）：
        - 融券费按**时点**计提一次，否则 N 个标的会重复计提 N 次；
        - TIF 过期在全部标的撮合完后统一处理，否则 A 标的的撮合会顺手撤掉 B 的挂单；
        - 标的顺序固定：**先撮合有卖单的标的**，让调仓释放的现金能被同一时点的买单用上，
          再按字母序撮合其余标的，保证可复现。

        单标的（len(bars) == 1）时三点均退化为无操作，逐笔行为与 `process_bar()` 一致。
        """
        self._accrue_borrow_fee(ts)
        self._last_bar_time = ts

        fills: list[Fill] = []
        for symbol in self._matching_sequence(bars):
            fills.extend(self._match_symbol(bars[symbol]))

        for symbol, bar in bars.items():
            self._last_prices[symbol] = bar.close

        if fills and self._account_controls:
            self._run_account_controls(ts)

        self._expire_orders(ts.date())
        return fills

    def _matching_sequence(self, bars: Mapping[str, Bar]) -> list[str]:
        """标的撮合次序：有卖单的优先（释放现金），同组内按字母序。"""
        if len(bars) < 2:
            return list(bars)
        selling = {o.symbol for o in self._pending if o.side is OrderSide.SELL}
        return sorted(bars, key=lambda s: (s not in selling, s))

    # ── 持仓上限 ─────────────────────────────────────────────

    def submit_order(self, order: Order) -> Order:
        """在既有校验之上追加同时持仓上限（未配置时零开销）。"""
        capped = self._max_open_positions is not None and order.qty > 0
        if capped and self._exceeds_open_limit(order):
            order.status = OrderStatus.REJECTED
            order.reject_reason = (
                f"同时持仓上限 {self._max_open_positions}：{order.symbol} 为新开标的，已拒单"
            )
            logger.debug("持仓上限拒单 %s", order.reject_reason)
            return order
        return super().submit_order(order)

    def _exceeds_open_limit(self, order: Order) -> bool:
        """该委托是否会让占用名额的标的数超过上限。"""
        if not self.positions.get(order.symbol).is_empty:
            return False   # 已有仓位，加减仓不占新名额
        reserved = self._reserved_symbols()
        if order.symbol in reserved:
            return False   # 同一标的已有挂单占位
        return len(reserved) >= (self._max_open_positions or 0)

    def _reserved_symbols(self) -> set[str]:
        """
        占用持仓名额的标的 = 「当前持仓 + 全部挂单」执行完之后**仍非零**的那些。

        必须看**执行后**的持仓而不是当下持仓：`target_weight()` 调仓时会在同一时点
        先挂清仓卖单、再挂新标的买单，若按当下持仓计数，正在被清掉的老标的仍占着
        名额，新标的会被误拒 —— 轮动策略于是安静地少持一只票。
        """
        projected = {s: self.positions.get(s).qty for s in self.positions.open_symbols}
        for pending in self._pending:
            delta = pending.qty if pending.side is OrderSide.BUY else -pending.qty
            projected[pending.symbol] = projected.get(pending.symbol, 0) + delta
        return {symbol for symbol, qty in projected.items() if qty != 0}
