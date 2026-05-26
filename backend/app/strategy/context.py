"""
策略上下文

每次 on_bar() 调用时，引擎将当前状态封装为 StrategyContext 传入策略。
策略通过 context 下单、查询持仓、获取历史数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from app.data.models import Bar, Market
from app.engine.backtest.broker import Order, SimulatedBroker
from app.engine.backtest.position import Position

if TYPE_CHECKING:
    pass


@dataclass
class StrategyContext:
    """
    传给 on_bar() 的上下文快照。

    bar      — 当前这根 K 线数据
    history  — 包含当前 bar 在内的所有历史 bar（DataFrame）
    broker   — 模拟券商，可调用 buy/sell
    """

    bar: Bar
    history: pd.DataFrame
    broker: SimulatedBroker

    # ── 账户状态（快捷访问）─────────────────────────────────

    @property
    def cash(self) -> float:
        return self.broker.cash

    @property
    def current_prices(self) -> dict[str, float]:
        return {self.bar.symbol: self.bar.close}

    @property
    def portfolio_value(self) -> float:
        return self.broker.portfolio_value(self.current_prices)

    def position(self, symbol: str | None = None) -> Position:
        """返回指定标的的持仓，默认返回当前 bar 标的的持仓。"""
        sym = symbol or self.bar.symbol
        return self.broker.positions.get(sym)

    @property
    def qty(self) -> int:
        """当前 bar 标的的持仓数量（快捷方式）。"""
        return self.position().qty

    # ── 下单接口 ─────────────────────────────────────────────

    def buy(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
    ) -> Order:
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        return self.broker.buy(sym, qty, mkt)

    def sell(
        self,
        qty: int,
        symbol: str | None = None,
        market: Market | None = None,
    ) -> Order:
        sym = symbol or self.bar.symbol
        mkt = market or self.bar.market
        return self.broker.sell(sym, qty, mkt)

    def buy_value(self, value: float, symbol: str | None = None) -> Order | None:
        """按金额买入，自动计算股数（向下取整）。"""
        price = self.bar.close
        if price <= 0:
            return None
        qty = int(value / price)
        if qty <= 0:
            return None
        return self.buy(qty, symbol)

    def sell_all(self, symbol: str | None = None) -> Order | None:
        """清空指定标的的全部持仓。"""
        sym = symbol or self.bar.symbol
        qty = self.broker.positions.get(sym).qty
        if qty <= 0:
            return None
        return self.sell(qty, sym)

    # ── 历史数据访问 ─────────────────────────────────────────

    def close_series(self, n: int | None = None) -> pd.Series:
        """返回收盘价序列，n 为取最近 n 根（默认全部）。"""
        closes = self.history["close"]
        return closes.iloc[-n:] if n else closes

    def volume_series(self, n: int | None = None) -> pd.Series:
        volumes = self.history["volume"]
        return volumes.iloc[-n:] if n else volumes
