"""
纸面交易网关（Paper Trading Gateway）

在没有真实券商配置时提供演示/测试下单功能。
- 市价单：立即成交（使用最近行情估算价格）
- 限价单：挂单状态（不自动撮合）
- 内存维护持仓与账户余额

参考: freqtrade dry_run 模式
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.gateway.base import AccountInfo, BrokerPosition, TradingGateway
from app.oms.order import LiveFill, LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType

logger = logging.getLogger(__name__)

_FILL_COMMISSION_RATE = 0.0003   # 0.03% 模拟佣金


class PaperGateway(TradingGateway):
    """
    纸面交易网关。

    - 市价单：立即以当前价格模拟成交
    - 限价单：保持挂单状态（不自动撮合，由用户手动撤单）
    - 持仓/账户余额完全在内存中维护
    """

    def __init__(
        self,
        market: str,
        initial_cash: float = 1_000_000.0,
        currency: str = "USD",
    ) -> None:
        self.market = market.upper()
        self._cash = initial_cash
        self._currency = currency
        self._connected = False

        # symbol → BrokerPosition
        self._positions: dict[str, BrokerPosition] = {}
        # 模拟当前价格缓存 symbol → price
        self._prices: dict[str, float] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    async def connect(self) -> None:
        self._connected = True
        logger.info("PaperGateway[%s] connected (paper trading mode)", self.market)

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── 下单接口 ──────────────────────────────────────────────

    async def submit_order(self, order: LiveOrder) -> str:
        broker_id = f"PAPER-{uuid.uuid4().hex[:12].upper()}"

        if order.order_type == LiveOrderType.MARKET:
            fill_price = self._estimate_price(order.symbol)
            self._apply_fill(order, fill_price, order.qty)

        return broker_id

    async def cancel_order(self, broker_order_id: str) -> None:
        # Paper orders can always be cancelled — caller updates status
        pass

    async def get_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "filled"}

    async def get_open_orders(self) -> list[dict]:
        return []

    # ── 账户/持仓 ─────────────────────────────────────────────

    async def get_account(self) -> AccountInfo:
        portfolio_value = self._cash + sum(
            (p.market_value or 0) for p in self._positions.values()
        )
        currency_map = {"US": "USD", "HK": "HKD", "A": "CNY"}
        return AccountInfo(
            account_id=f"PAPER-{self.market}",
            currency=currency_map.get(self.market, "USD"),
            cash=round(self._cash, 2),
            buying_power=round(self._cash, 2),
            portfolio_value=round(portfolio_value, 2),
        )

    async def get_positions(self) -> list[BrokerPosition]:
        # Refresh market values with cached prices
        result = []
        for sym, pos in self._positions.items():
            price = self._prices.get(sym, pos.avg_cost)
            mv = price * pos.qty
            upnl = (price - pos.avg_cost) * pos.qty
            result.append(BrokerPosition(
                symbol=pos.symbol,
                market=pos.market,
                qty=pos.qty,
                avg_cost=pos.avg_cost,
                current_price=price,
                market_value=round(mv, 2),
                unrealized_pnl=round(upnl, 2),
            ))
        return [p for p in result if p.qty != 0]

    # ── 价格与持仓更新 ────────────────────────────────────────

    def update_price(self, symbol: str, price: float) -> None:
        """外部可调用：更新行情缓存（例如推送实时价格时）。"""
        self._prices[symbol] = price

    def _estimate_price(self, symbol: str) -> float:
        """
        估算成交价：
        1. 使用已有行情缓存
        2. 否则根据市场使用占位默认价格
        """
        if symbol in self._prices:
            return self._prices[symbol]
        # 合理的默认价格以便演示可用
        defaults = {
            "AAPL": 175.0, "TSLA": 250.0, "NVDA": 800.0, "SPY": 520.0,
            "QQQ": 440.0, "AMZN": 190.0, "MSFT": 420.0, "GOOGL": 170.0,
            "00700": 350.0, "02318": 70.0,
            "000001": 12.0, "600519": 1500.0, "300750": 220.0,
        }
        return defaults.get(symbol, 100.0)

    def _apply_fill(self, order: LiveOrder, price: float, qty: int) -> None:
        """模拟成交：更新持仓和现金。"""
        commission = round(price * qty * _FILL_COMMISSION_RATE, 2)

        if order.side == LiveOrderSide.BUY:
            cost = price * qty + commission
            self._cash -= cost
            self._update_position_buy(order.symbol, qty, price)
        else:
            proceeds = price * qty - commission
            self._cash += proceeds
            self._update_position_sell(order.symbol, qty, price)

        order.filled_qty = qty
        order.avg_fill_price = price
        order.commission = commission
        order.status = LiveOrderStatus.FILLED
        order.filled_at = datetime.now(timezone.utc)
        self._prices[order.symbol] = price

        logger.info(
            "PaperGateway fill: %s %s %d @ %.4f, commission=%.2f",
            order.side.value, order.symbol, qty, price, commission,
        )

    def _update_position_buy(self, symbol: str, qty: int, price: float) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            self._positions[symbol] = BrokerPosition(
                symbol=symbol,
                market=self.market,
                qty=qty,
                avg_cost=price,
            )
        else:
            new_qty = pos.qty + qty
            new_cost = (pos.avg_cost * pos.qty + price * qty) / new_qty
            self._positions[symbol] = BrokerPosition(
                symbol=symbol,
                market=self.market,
                qty=new_qty,
                avg_cost=round(new_cost, 4),
            )

    def _update_position_sell(self, symbol: str, qty: int, price: float) -> None:
        pos = self._positions.get(symbol)
        if pos is None:
            logger.warning("Sell on non-existent position: %s", symbol)
            return
        new_qty = pos.qty - qty
        if new_qty < 0:
            new_qty = 0
        self._positions[symbol] = BrokerPosition(
            symbol=symbol,
            market=self.market,
            qty=new_qty,
            avg_cost=pos.avg_cost if new_qty > 0 else 0.0,
        )
