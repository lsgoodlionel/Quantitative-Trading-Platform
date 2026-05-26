"""
模拟撮合引擎

回测中模拟真实券商的订单处理流程。订单在下一根 K 线开盘价成交
（next-bar fill），避免偷看当根 K 线数据（look-ahead bias）。

参考: refs/backtrader/backtrader/broker.py 的 BackBroker 设计。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
import uuid

from app.data.models import Bar, Market
from app.engine.backtest.commission import get_commission_model, CommissionModel
from app.engine.backtest.position import PortfolioPositions
from app.engine.backtest.slippage import get_slippage_model, SlippageModel

if TYPE_CHECKING:
    pass


class OrderStatus(str, Enum):
    PENDING = "pending"     # 等待下一根 bar 撮合
    FILLED = "filled"       # 已成交
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Order:
    symbol: str
    market: Market
    side: OrderSide
    qty: int
    order_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    filled_price: float | None = None
    filled_at: datetime | None = None
    commission: float = 0.0
    reject_reason: str | None = None


@dataclass
class Fill:
    order_id: str
    symbol: str
    market: Market
    side: OrderSide
    qty: int
    price: float
    commission: float
    filled_at: datetime
    realized_pnl: float = 0.0


class SimulatedBroker:
    """
    模拟券商，维护现金、持仓、挂单队列。

    撮合规则:
    - 市价单在下一根 bar 开盘价成交（next-bar open fill）
    - 成交价经过滑点模型调整
    - 成交产生 Fill 事件，更新持仓和现金
    """

    def __init__(
        self,
        initial_cash: float,
        market: Market,
        commission_model: CommissionModel | None = None,
        slippage_model: SlippageModel | None = None,
    ) -> None:
        self._cash = initial_cash
        self._initial_cash = initial_cash
        self._market = market
        self._commission = commission_model or get_commission_model(market)
        self._slippage = slippage_model or get_slippage_model(market)
        self._positions = PortfolioPositions()
        self._pending: list[Order] = []
        self._fills: list[Fill] = []

    # ── 账户状态 ──────────────────────────────────────────────

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> PortfolioPositions:
        return self._positions

    @property
    def fills(self) -> list[Fill]:
        return self._fills

    def portfolio_value(self, prices: dict[str, float]) -> float:
        return self._cash + self._positions.total_market_value(prices)

    # ── 下单 ─────────────────────────────────────────────────

    def submit_order(self, order: Order) -> Order:
        """将订单加入挂单队列，返回带 order_id 的 Order 对象。"""
        if order.qty <= 0:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "qty must be positive"
            return order

        pos_qty = self._positions.get(order.symbol).qty
        if order.side == OrderSide.SELL and order.qty > pos_qty:
            order.status = OrderStatus.REJECTED
            order.reject_reason = (
                f"insufficient position: need {order.qty}, have {pos_qty}"
            )
            return order

        self._pending.append(order)
        return order

    def buy(self, symbol: str, qty: int, market: Market | None = None) -> Order:
        order = Order(
            symbol=symbol,
            market=market or self._market,
            side=OrderSide.BUY,
            qty=qty,
        )
        return self.submit_order(order)

    def sell(self, symbol: str, qty: int, market: Market | None = None) -> Order:
        order = Order(
            symbol=symbol,
            market=market or self._market,
            side=OrderSide.SELL,
            qty=qty,
        )
        return self.submit_order(order)

    # ── 撮合 ─────────────────────────────────────────────────

    def process_bar(self, bar: Bar) -> list[Fill]:
        """
        用当根 bar 撮合上一根 bar 挂入的订单（next-bar open fill）。
        返回本次产生的所有 Fill。
        """
        new_fills: list[Fill] = []
        remaining: list[Order] = []

        for order in self._pending:
            if order.symbol != bar.symbol:
                remaining.append(order)
                continue

            fill = self._try_fill(order, bar)
            if fill is not None:
                new_fills.append(fill)
                self._fills.append(fill)
                order.status = OrderStatus.FILLED
                order.filled_price = fill.price
                order.filled_at = fill.filled_at
                order.commission = fill.commission
            else:
                remaining.append(order)

        self._pending = remaining
        return new_fills

    def _try_fill(self, order: Order, bar: Bar) -> Fill | None:
        fill_price_raw = bar.open
        fill_price = self._slippage.apply(fill_price_raw, order.side.value, bar)

        comm_result = self._commission.calculate(fill_price, order.qty, order.side.value)
        total_cost = comm_result.total

        if order.side == OrderSide.BUY:
            trade_value = fill_price * order.qty
            total_outflow = trade_value + total_cost
            if total_outflow > self._cash:
                # 现金不足：调整股数
                max_qty = int(self._cash / (fill_price * (1 + 0.005)))  # 留 0.5% 余量
                if max_qty <= 0:
                    order.status = OrderStatus.REJECTED
                    order.reject_reason = "insufficient cash"
                    return None
                order.qty = max_qty
                comm_result = self._commission.calculate(fill_price, order.qty, order.side.value)
                total_cost = comm_result.total
                total_outflow = fill_price * order.qty + total_cost

            self._cash -= total_outflow
            self._positions.buy(order.symbol, order.qty, fill_price, total_cost)
            realized_pnl = 0.0
        else:
            realized_pnl = self._positions.sell(
                order.symbol, order.qty, fill_price, total_cost
            )
            trade_value = fill_price * order.qty
            self._cash += trade_value - total_cost

        return Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            market=order.market,
            side=order.side,
            qty=order.qty,
            price=fill_price,
            commission=total_cost,
            filled_at=bar.time,
            realized_pnl=realized_pnl,
        )

    def cancel_all_pending(self) -> int:
        count = len(self._pending)
        for order in self._pending:
            order.status = OrderStatus.CANCELLED
        self._pending = []
        return count

    def snapshot(self, prices: dict[str, float]) -> dict:
        return {
            "cash": round(self._cash, 2),
            "portfolio_value": round(self.portfolio_value(prices), 2),
            "total_return_pct": round(
                (self.portfolio_value(prices) - self._initial_cash) / self._initial_cash * 100, 4
            ),
            "positions": self._positions.snapshot(prices),
            "pending_orders": len(self._pending),
            "total_fills": len(self._fills),
        }
