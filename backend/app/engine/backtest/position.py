"""
持仓管理 — FIFO 成本法

参考 refs/zipline-reloaded/zipline/finance/position.py 的持仓计算设计。
使用 FIFO（先进先出）成本法计算已实现盈亏，与大多数券商一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections import deque


@dataclass
class _Lot:
    """一笔买入记录（FIFO队列中的一个元素）。"""
    qty: int
    cost: float  # 含佣金的单股成本


@dataclass
class Position:
    """
    单个标的持仓状态，维护 FIFO 成本队列。

    不可变规则: Position 本身可变（因为持仓会增减），
    但每次操作返回新的 Position 副本，或直接更新 qty/avg_cost。
    这里采用类内状态更新方式，因为持仓本质上是一个持续演化的账本。

    A股 T+1 规则：今日买入的股票当日不可卖出（non_closable）。
    每个新交易日开始时通过 advance_day() 清空 non_closable。
    参考: refs/rqalpha/rqalpha/mod/rqalpha_mod_sys_accounts/position_model.py
    """

    symbol: str
    _lots: deque[_Lot] = field(default_factory=deque)
    realized_pnl: float = 0.0
    _non_closable: int = field(default=0)  # T+1: 今日买入尚不可卖出的数量

    @property
    def qty(self) -> int:
        return sum(lot.qty for lot in self._lots)

    @property
    def avg_cost(self) -> float:
        total_qty = self.qty
        if total_qty == 0:
            return 0.0
        total_cost = sum(lot.qty * lot.cost for lot in self._lots)
        return total_cost / total_qty

    @property
    def is_empty(self) -> bool:
        return self.qty == 0

    @property
    def closable_qty(self) -> int:
        """可卖出数量（扣除 T+1 限制）。"""
        return max(0, self.qty - self._non_closable)

    def advance_day(self) -> None:
        """新交易日开始：解除 T+1 限制（昨日买入今日可卖）。"""
        self._non_closable = 0

    def add(self, qty: int, price: float, commission: float = 0.0, t_plus: bool = False) -> None:
        """开仓/加仓：将新买入加入 FIFO 队列。t_plus=True 时启用T+1限制。"""
        cost_per_share = price + commission / qty if qty > 0 else price
        self._lots.append(_Lot(qty=qty, cost=cost_per_share))
        if t_plus:
            self._non_closable += qty

    def reduce(self, qty: int, price: float, commission: float = 0.0) -> float:
        """
        减仓/平仓：FIFO 消耗持仓，返回本次已实现盈亏。

        卖出时先消耗最早买入的批次（FIFO）。
        """
        if qty > self.qty:
            raise ValueError(
                f"Cannot sell {qty} shares of {self.symbol}, only {self.qty} held"
            )

        remaining = qty
        realized = 0.0
        commission_per_share = commission / qty if qty > 0 else 0.0

        while remaining > 0 and self._lots:
            lot = self._lots[0]
            consumed = min(remaining, lot.qty)

            # 每股盈亏 = 卖价 - 买入成本 - 卖出佣金摊薄
            pnl_per_share = price - lot.cost - commission_per_share
            realized += pnl_per_share * consumed

            lot.qty -= consumed
            remaining -= consumed

            if lot.qty == 0:
                self._lots.popleft()

        self.realized_pnl += realized
        return realized

    def market_value(self, current_price: float) -> float:
        return current_price * self.qty

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.avg_cost) * self.qty


class PortfolioPositions:
    """多标的持仓账本。"""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}

    def get(self, symbol: str) -> Position:
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        return self._positions[symbol]

    def advance_day(self) -> None:
        """新交易日开始：解除所有持仓的 T+1 限制。"""
        for pos in self._positions.values():
            pos.advance_day()

    def buy(self, symbol: str, qty: int, price: float, commission: float = 0.0, t_plus: bool = False) -> None:
        self.get(symbol).add(qty, price, commission, t_plus=t_plus)

    def sell(self, symbol: str, qty: int, price: float, commission: float = 0.0) -> float:
        return self.get(symbol).reduce(qty, price, commission)

    def total_market_value(self, prices: dict[str, float]) -> float:
        return sum(
            pos.market_value(prices.get(pos.symbol, pos.avg_cost))
            for pos in self._positions.values()
            if not pos.is_empty
        )

    def total_unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            pos.unrealized_pnl(prices.get(pos.symbol, pos.avg_cost))
            for pos in self._positions.values()
            if not pos.is_empty
        )

    def total_realized_pnl(self) -> float:
        return sum(pos.realized_pnl for pos in self._positions.values())

    def snapshot(self, prices: dict[str, float]) -> list[dict]:
        result = []
        for symbol, pos in self._positions.items():
            if pos.is_empty:
                continue
            price = prices.get(symbol, pos.avg_cost)
            result.append({
                "symbol": symbol,
                "qty": pos.qty,
                "avg_cost": round(pos.avg_cost, 4),
                "current_price": price,
                "market_value": round(pos.market_value(price), 2),
                "unrealized_pnl": round(pos.unrealized_pnl(price), 2),
                "realized_pnl": round(pos.realized_pnl, 2),
            })
        return result
