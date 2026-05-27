"""
策略上下文

每次 on_bar() 调用时，引擎将当前状态封装为 StrategyContext 传入策略。
策略通过 context 下单、查询持仓、获取历史数据。

支持两种模式:
- 回测模式: broker = SimulatedBroker（内存模拟）
- 实盘模式: broker = None, live_order_ctx = LiveOrderContext（路由到 OMS）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

import pandas as pd

from app.data.models import Bar, Market
from app.engine.backtest.broker import Order, SimulatedBroker
from app.engine.backtest.position import Position

if TYPE_CHECKING:
    from app.strategy.engine import LiveOrderContext


@dataclass
class StrategyContext:
    """
    传给 on_bar() 的上下文快照。

    bar            — 当前这根 K 线数据
    history        — 包含当前 bar 在内的所有历史 bar（DataFrame）
    broker         — 回测模式：模拟券商；实盘模式：None
    live_order_ctx — 实盘模式：LiveOrderContext，负责将信号路由到 OMS
    """

    bar: Bar
    history: pd.DataFrame
    broker: Optional[SimulatedBroker]
    live_order_ctx: Optional["LiveOrderContext"] = field(default=None)

    # ── 模式判断 ─────────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        """True = 实盘/模拟盘模式，False = 回测模式。"""
        return self.broker is None

    # ── 账户状态（快捷访问）─────────────────────────────────

    @property
    def cash(self) -> float:
        if self.broker is None:
            return float("inf")   # 实盘模式不限制（由 OMS 负责）
        return self.broker.cash

    @property
    def current_prices(self) -> dict[str, float]:
        return {self.bar.symbol: self.bar.close}

    @property
    def portfolio_value(self) -> float:
        if self.broker is None:
            return 0.0
        return self.broker.portfolio_value(self.current_prices)

    def position(self, symbol: str | None = None) -> Optional[Position]:
        """返回指定标的的持仓，实盘模式返回 None（由 OMS 管理）。"""
        if self.broker is None:
            return None
        sym = symbol or self.bar.symbol
        return self.broker.positions.get(sym)

    @property
    def qty(self) -> int:
        """当前 bar 标的的持仓数量（快捷方式）。实盘模式返回 0。"""
        pos = self.position()
        return pos.qty if pos else 0

    # ── 下单接口 ─────────────────────────────────────────────

    def buy(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> Optional[Order]:
        if self.is_live:
            if self.live_order_ctx:
                self.live_order_ctx.buy(qty, order_type=order_type, limit_price=limit_price)
            return None
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        return self.broker.buy(sym, qty, mkt)  # type: ignore[union-attr]

    def sell(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
        order_type: str = "MARKET",
        limit_price: Optional[float] = None,
    ) -> Optional[Order]:
        if self.is_live:
            if self.live_order_ctx:
                self.live_order_ctx.sell(qty, order_type=order_type, limit_price=limit_price)
            return None
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        return self.broker.sell(sym, qty, mkt)  # type: ignore[union-attr]

    def buy_value(self, value: float, symbol: str | None = None) -> Optional[Order]:
        """按金额买入，自动计算股数（向下取整）。"""
        price = self.bar.close
        if price <= 0:
            return None
        qty = int(value / price)
        if qty <= 0:
            return None
        return self.buy(qty, symbol)

    def sell_all(self, symbol: str | None = None) -> Optional[Order]:
        """清空指定标的的全部持仓。"""
        if self.is_live:
            # 实盘模式：发出足够大的卖出信号，由 OMS 处理数量
            if self.live_order_ctx:
                self.live_order_ctx.sell(9999)
            return None
        sym = symbol or self.bar.symbol
        pos = self.broker.positions.get(sym)  # type: ignore[union-attr]
        if not pos or pos.qty <= 0:
            return None
        return self.sell(pos.qty, sym)

    # ── 历史数据访问 ─────────────────────────────────────────

    def close_series(self, n: int | None = None) -> pd.Series:
        """返回收盘价序列，n 为取最近 n 根（默认全部）。"""
        closes = self.history["close"]
        return closes.iloc[-n:] if n else closes

    def volume_series(self, n: int | None = None) -> pd.Series:
        volumes = self.history["volume"]
        return volumes.iloc[-n:] if n else volumes
