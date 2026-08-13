"""持仓管理 — FIFO 成本法（支持多空双向）

参考 refs/zipline-reloaded/zipline/finance/position.py 的持仓计算设计。
使用 FIFO（先进先出）成本法计算已实现盈亏，与大多数券商一致。

K2 起持仓拆成 `_long_lots` / `_short_lots` 两条独立 FIFO 队列：
- `qty` = long_qty - short_qty，可为负
- 空头批次的 `cost` 记「开仓净收入/股」（卖出价扣掉开仓费用），
  平空盈亏 = 开仓净收入 - 平仓价 - 平仓费用摊薄，与多头完全对称
- 引擎保证同一标的不会同时持有多头与空头批次（反向委托先平后开）
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["long", "short", "flat"]


@dataclass
class _Lot:
    """一笔开仓记录（FIFO队列中的一个元素）。"""
    qty: int
    cost: float  # 多头：含佣金的单股成本；空头：扣费后的单股净收入


@dataclass
class Position:
    """
    单个标的持仓状态，维护多空两条 FIFO 成本队列。

    不可变规则: Position 本身可变（因为持仓会增减），
    但每次操作返回新的 Position 副本，或直接更新 qty/avg_cost。
    这里采用类内状态更新方式，因为持仓本质上是一个持续演化的账本。

    A股 T+1 规则：今日买入的股票当日不可卖出（non_closable）。
    每个新交易日开始时通过 advance_day() 清空 non_closable。
    参考: refs/rqalpha/rqalpha/mod/rqalpha_mod_sys_accounts/position_model.py
    A股不开放做空（融券不在本期范围），因此 T+1 只作用于多头队列。
    """

    symbol: str
    _lots: deque[_Lot] = field(default_factory=deque)          # 多头 FIFO 队列
    realized_pnl: float = 0.0
    _non_closable: int = field(default=0)  # T+1: 今日买入尚不可卖出的数量
    _short_lots: deque[_Lot] = field(default_factory=deque)    # 空头 FIFO 队列

    # ── 数量与方向 ───────────────────────────────────────────

    @property
    def long_qty(self) -> int:
        return sum(lot.qty for lot in self._lots)

    @property
    def short_qty(self) -> int:
        return sum(lot.qty for lot in self._short_lots)

    @property
    def qty(self) -> int:
        """带符号净持仓：多头为正，空头为负。"""
        return self.long_qty - self.short_qty

    @property
    def direction(self) -> Direction:
        net = self.qty
        if net > 0:
            return "long"
        if net < 0:
            return "short"
        return "flat"

    @property
    def avg_cost(self) -> float:
        """按当前方向的队列计算平均成本（空头为平均开仓净收入）。"""
        lots = self._short_lots if self.direction == "short" else self._lots
        total_qty = sum(lot.qty for lot in lots)
        if total_qty == 0:
            return 0.0
        return sum(lot.qty * lot.cost for lot in lots) / total_qty

    @property
    def is_empty(self) -> bool:
        return not self._lots and not self._short_lots

    @property
    def closable_qty(self) -> int:
        """可卖出数量（多头持仓扣除 A股 T+1 限制）。"""
        return max(0, self.long_qty - self._non_closable)

    @property
    def short_notional(self) -> float:
        """空头名义金额（按开仓净收入计），用于计提融券费。"""
        return sum(lot.qty * lot.cost for lot in self._short_lots)

    def advance_day(self) -> None:
        """新交易日开始：解除 T+1 限制（昨日买入今日可卖）。"""
        self._non_closable = 0

    # ── 多头 ────────────────────────────────────────────────

    def add(self, qty: int, price: float, commission: float = 0.0, t_plus: bool = False) -> None:
        """开仓/加仓：将新买入加入 FIFO 队列。t_plus=True 时启用T+1限制。"""
        cost_per_share = price + commission / qty if qty > 0 else price
        self._lots.append(_Lot(qty=qty, cost=cost_per_share))
        if t_plus:
            self._non_closable += qty

    def reduce(self, qty: int, price: float, commission: float = 0.0) -> float:
        """
        减仓/平仓：FIFO 消耗多头持仓，返回本次已实现盈亏。

        卖出时先消耗最早买入的批次（FIFO）。
        """
        if qty > self.long_qty:
            raise ValueError(
                f"Cannot sell {qty} shares of {self.symbol}, only {self.long_qty} held"
            )
        return self._consume(self._lots, qty, price, commission, is_long=True)

    # ── 空头 ────────────────────────────────────────────────

    def add_short(self, qty: int, price: float, commission: float = 0.0) -> None:
        """开空/加空：记录本批次的单股净收入（卖出价扣掉开仓费用）。"""
        proceeds_per_share = price - commission / qty if qty > 0 else price
        self._short_lots.append(_Lot(qty=qty, cost=proceeds_per_share))

    def reduce_short(self, qty: int, price: float, commission: float = 0.0) -> float:
        """平空：FIFO 消耗空头批次，返回本次已实现盈亏。"""
        if qty > self.short_qty:
            raise ValueError(
                f"Cannot cover {qty} shares of {self.symbol}, only {self.short_qty} shorted"
            )
        return self._consume(self._short_lots, qty, price, commission, is_long=False)

    def _consume(
        self,
        lots: deque[_Lot],
        qty: int,
        price: float,
        commission: float,
        *,
        is_long: bool,
    ) -> float:
        """FIFO 消耗批次并累计已实现盈亏（多空共用，只差一个符号）。"""
        remaining = qty
        realized = 0.0
        commission_per_share = commission / qty if qty > 0 else 0.0

        while remaining > 0 and lots:
            lot = lots[0]
            consumed = min(remaining, lot.qty)

            # 多头：卖价 - 买入成本 - 卖出佣金摊薄
            # 空头：开仓净收入 - 平仓价 - 平仓佣金摊薄
            if is_long:
                pnl_per_share = price - lot.cost - commission_per_share
            else:
                pnl_per_share = lot.cost - price - commission_per_share
            realized += pnl_per_share * consumed

            lot.qty -= consumed
            remaining -= consumed

            if lot.qty == 0:
                lots.popleft()

        self.realized_pnl += realized
        return realized

    # ── 估值 ────────────────────────────────────────────────

    def market_value(self, current_price: float) -> float:
        """带符号市值：空头为负。"""
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

    @property
    def open_symbols(self) -> list[str]:
        """当前有持仓（多或空）的标的（K1 组合引擎的持仓上限计数口径）。"""
        return [symbol for symbol, pos in self._positions.items() if not pos.is_empty]

    def net_quantities(self) -> dict[str, int]:
        """全部非空持仓的带符号数量快照（K1 日度盈亏的起始仓位）。"""
        return {
            symbol: pos.qty
            for symbol, pos in self._positions.items()
            if not pos.is_empty
        }

    def advance_day(self) -> None:
        """新交易日开始：解除所有持仓的 T+1 限制。"""
        for pos in self._positions.values():
            pos.advance_day()

    def buy(self, symbol: str, qty: int, price: float, commission: float = 0.0, t_plus: bool = False) -> None:
        self.get(symbol).add(qty, price, commission, t_plus=t_plus)

    def sell(self, symbol: str, qty: int, price: float, commission: float = 0.0) -> float:
        return self.get(symbol).reduce(qty, price, commission)

    def short(self, symbol: str, qty: int, price: float, commission: float = 0.0) -> None:
        self.get(symbol).add_short(qty, price, commission)

    def cover(self, symbol: str, qty: int, price: float, commission: float = 0.0) -> float:
        return self.get(symbol).reduce_short(qty, price, commission)

    def total_market_value(self, prices: dict[str, float]) -> float:
        # 注意不要写成 `prices.get(symbol, pos.avg_cost)`：dict.get 的默认值是**立即求值**的，
        # 那样每次估值都要为每个标的白算一遍 avg_cost（组合回测下是 O(标的×时点×批次)）。
        return sum(
            pos.market_value(self._mark_price(pos, prices))
            for pos in self._positions.values()
            if not pos.is_empty
        )

    def total_unrealized_pnl(self, prices: dict[str, float]) -> float:
        return sum(
            pos.unrealized_pnl(self._mark_price(pos, prices))
            for pos in self._positions.values()
            if not pos.is_empty
        )

    @staticmethod
    def _mark_price(pos: Position, prices: dict[str, float]) -> float:
        """估值价：优先用外部给定价（组合引擎传的是最后已知价），否则回退到成本价。"""
        price = prices.get(pos.symbol)
        return pos.avg_cost if price is None else price

    def total_realized_pnl(self) -> float:
        return sum(pos.realized_pnl for pos in self._positions.values())

    def total_short_notional(self) -> float:
        """所有标的的空头名义金额合计。"""
        return sum(pos.short_notional for pos in self._positions.values())

    def snapshot(self, prices: dict[str, float]) -> list[dict]:
        result = []
        for symbol, pos in self._positions.items():
            if pos.is_empty:
                continue
            price = prices.get(symbol, pos.avg_cost)
            result.append({
                "symbol": symbol,
                "qty": pos.qty,
                "direction": pos.direction,
                "avg_cost": round(pos.avg_cost, 4),
                "current_price": price,
                "market_value": round(pos.market_value(price), 2),
                "unrealized_pnl": round(pos.unrealized_pnl(price), 2),
                "realized_pnl": round(pos.realized_pnl, 2),
            })
        return result
