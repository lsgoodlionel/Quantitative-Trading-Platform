"""
Alpaca 美股实盘网关

参考:
  refs/alpaca-trade-api-python/alpaca_trade_api/  — REST + WebSocket
  Alpaca Trading API v2: https://docs.alpaca.markets/reference

沙盒/实盘切换由 settings.alpaca_paper 控制:
  True  → paper-api.alpaca.markets
  False → api.alpaca.markets
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.gateway.base import TradingGateway, AccountInfo, BrokerPosition
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType

logger = logging.getLogger(__name__)

# Alpaca SDK 是同步库，所有调用包裹在 run_in_executor 中
_EXECUTOR = None  # 使用默认 ThreadPoolExecutor


def _get_trading_client():
    """延迟导入 + 懒加载，避免没安装 alpaca-py 时启动失败。"""
    try:
        from alpaca.trading.client import TradingClient
        return TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )
    except ImportError as e:
        raise RuntimeError(
            "alpaca-py not installed. Run: pip install alpaca-py"
        ) from e


class AlpacaGateway(TradingGateway):
    """
    Alpaca 实盘网关（美股）。

    特性:
    - 支持沙盒（paper）和实盘切换
    - 零佣金，含 SEC/FINRA 规费
    - 美股市价单/限价单
    """

    def __init__(self) -> None:
        self._client = None
        self._connected = False

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        self._client = await loop.run_in_executor(None, _get_trading_client)
        # 验证连接：拉取账户信息
        await self.get_account()
        self._connected = True
        mode = "PAPER" if settings.alpaca_paper else "LIVE"
        logger.info("Alpaca gateway connected (%s mode)", mode)

    async def disconnect(self) -> None:
        self._client = None
        self._connected = False
        logger.info("Alpaca gateway disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require_client(self):
        if self._client is None:
            raise RuntimeError("Alpaca gateway not connected. Call connect() first.")
        return self._client

    async def submit_order(self, order: LiveOrder) -> str:
        client = self._require_client()

        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        loop = asyncio.get_event_loop()

        side = OrderSide.BUY if order.side == LiveOrderSide.BUY else OrderSide.SELL

        if order.order_type == LiveOrderType.LIMIT and order.limit_price:
            req = LimitOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=TimeInForce.DAY,
                limit_price=order.limit_price,
            )
        else:
            req = MarketOrderRequest(
                symbol=order.symbol,
                qty=order.qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            )

        def _submit():
            return client.submit_order(order_data=req)

        try:
            result = await loop.run_in_executor(None, _submit)
            broker_id = str(result.id)
            logger.info(
                "Order submitted to Alpaca: %s %s x%d → broker_id=%s",
                order.side.value, order.symbol, order.qty, broker_id,
            )
            return broker_id
        except Exception as e:
            logger.error("Alpaca order submission failed: %s", e)
            raise

    async def cancel_order(self, broker_order_id: str) -> None:
        client = self._require_client()
        loop = asyncio.get_event_loop()

        def _cancel():
            import uuid as _uuid
            client.cancel_order_by_id(_uuid.UUID(broker_order_id))

        try:
            await loop.run_in_executor(None, _cancel)
            logger.info("Order cancelled: %s", broker_order_id)
        except Exception as e:
            logger.error("Alpaca cancel failed for %s: %s", broker_order_id, e)
            raise

    async def get_order(self, broker_order_id: str) -> dict:
        client = self._require_client()
        loop = asyncio.get_event_loop()

        def _get():
            import uuid as _uuid
            return client.get_order_by_id(_uuid.UUID(broker_order_id))

        result = await loop.run_in_executor(None, _get)
        return _alpaca_order_to_dict(result)

    async def get_open_orders(self) -> list[dict]:
        client = self._require_client()
        loop = asyncio.get_event_loop()

        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        def _get():
            req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
            return client.get_orders(filter=req)

        results = await loop.run_in_executor(None, _get)
        return [_alpaca_order_to_dict(o) for o in results]

    async def get_account(self) -> AccountInfo:
        client = self._require_client()
        loop = asyncio.get_event_loop()

        def _get():
            return client.get_account()

        acc = await loop.run_in_executor(None, _get)
        return AccountInfo(
            account_id=str(acc.id),
            currency=acc.currency,
            cash=float(acc.cash),
            buying_power=float(acc.buying_power),
            portfolio_value=float(acc.portfolio_value),
            day_trade_count=int(acc.daytrade_count or 0),
        )

    async def get_positions(self) -> list[BrokerPosition]:
        client = self._require_client()
        loop = asyncio.get_event_loop()

        def _get():
            return client.get_all_positions()

        positions = await loop.run_in_executor(None, _get)
        return [
            BrokerPosition(
                symbol=p.symbol,
                market="US",
                qty=int(p.qty),
                avg_cost=float(p.avg_entry_price),
                current_price=float(p.current_price) if p.current_price else None,
                market_value=float(p.market_value) if p.market_value else None,
                unrealized_pnl=float(p.unrealized_pl) if p.unrealized_pl else None,
            )
            for p in positions
        ]

    async def sync_order_status(self, order: LiveOrder) -> LiveOrderStatus:
        """拉取券商最新状态并返回对应的 LiveOrderStatus。"""
        if not order.broker_order_id:
            return order.status
        raw = await self.get_order(order.broker_order_id)
        return _map_alpaca_status(raw.get("status", ""))


def _alpaca_order_to_dict(order) -> dict:
    return {
        "broker_order_id": str(order.id),
        "symbol": order.symbol,
        "side": order.side.value if hasattr(order.side, "value") else str(order.side),
        "qty": int(order.qty or 0),
        "filled_qty": int(order.filled_qty or 0),
        "avg_fill_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        "status": str(order.status.value) if hasattr(order.status, "value") else str(order.status),
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "filled_at": order.filled_at.isoformat() if order.filled_at else None,
    }


_ALPACA_STATUS_MAP: dict[str, LiveOrderStatus] = {
    "new": LiveOrderStatus.SUBMITTED,
    "partially_filled": LiveOrderStatus.PARTIAL,
    "filled": LiveOrderStatus.FILLED,
    "done_for_day": LiveOrderStatus.CANCELLED,
    "canceled": LiveOrderStatus.CANCELLED,
    "expired": LiveOrderStatus.EXPIRED,
    "replaced": LiveOrderStatus.CANCELLED,
    "pending_cancel": LiveOrderStatus.SUBMITTED,
    "pending_replace": LiveOrderStatus.SUBMITTED,
    "held": LiveOrderStatus.SUBMITTED,
    "accepted": LiveOrderStatus.SUBMITTED,
    "pending_new": LiveOrderStatus.PENDING_SUBMIT,
    "rejected": LiveOrderStatus.REJECTED,
}


def _map_alpaca_status(raw_status: str) -> LiveOrderStatus:
    return _ALPACA_STATUS_MAP.get(raw_status.lower(), LiveOrderStatus.SUBMITTED)
