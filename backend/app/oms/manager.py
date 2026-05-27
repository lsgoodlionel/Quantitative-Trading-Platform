"""
OMS — 订单管理系统

负责实盘订单的完整生命周期：
  创建 → 风控前置检查 → 路由到券商网关 → 跟踪状态 → 推送成交事件

设计特点:
- 内存中维护活跃订单映射（生产环境应持久化到 PostgreSQL）
- 异步状态轮询（每 5 秒拉取活跃订单状态）
- 发出 Redis stream 事件供前端实时订阅

参考: refs/vnpy/vnpy/trader/engine.py MainEngine 的订单路由设计
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.gateway.base import TradingGateway
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType

logger = logging.getLogger(__name__)

# 活跃订单状态轮询间隔（秒）
_POLL_INTERVAL = 5.0
# 单次下单最大股数上限（防误操作）
MAX_ORDER_QTY = 100_000


class RiskViolation(Exception):
    """风控前置检查不通过时抛出。"""


class OrderManager:
    """
    实盘订单管理器。

    使用方式（FastAPI lifespan 内）:
        manager = OrderManager()
        manager.register_gateway("US", alpaca_gateway)
        manager.register_gateway("HK", futu_gateway)
        await manager.start()
        ...
        await manager.stop()
    """

    def __init__(self, redis_client=None) -> None:
        self._gateways: dict[str, TradingGateway] = {}
        self._orders: dict[str, LiveOrder] = {}   # order_id → LiveOrder
        self._redis = redis_client
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False

    # ── 网关注册 ──────────────────────────────────────────────

    def register_gateway(self, market: str, gateway: TradingGateway) -> None:
        self._gateways[market.upper()] = gateway
        logger.info("Gateway registered for market: %s", market)

    def get_gateway(self, market: str) -> TradingGateway:
        gw = self._gateways.get(market.upper())
        if gw is None:
            raise ValueError(f"No gateway registered for market '{market}'")
        return gw

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("OrderManager started")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("OrderManager stopped")

    # ── 下单接口 ──────────────────────────────────────────────

    async def submit_order(
        self,
        symbol: str,
        market: str,
        side: LiveOrderSide,
        qty: int,
        order_type: LiveOrderType = LiveOrderType.MARKET,
        limit_price: Optional[float] = None,
        strategy_id: Optional[str] = None,
    ) -> LiveOrder:
        """
        创建并提交实盘订单。

        1. 风控前置检查
        2. 路由到对应网关
        3. 记录到内存订单簿
        4. 推送事件
        """
        order = LiveOrder(
            symbol=symbol,
            market=market,
            side=side,
            qty=qty,
            order_type=order_type,
            limit_price=limit_price,
            strategy_id=strategy_id,
        )

        self._pre_trade_risk_check(order)

        gw = self.get_gateway(market)
        if not gw.is_connected:
            raise RuntimeError(f"Gateway for {market} is not connected")

        try:
            broker_order_id = await gw.submit_order(order)
            order.broker_order_id = broker_order_id
            # Gateway may have already filled the order (e.g. PaperGateway market orders)
            if order.status not in (LiveOrderStatus.FILLED, LiveOrderStatus.PARTIAL):
                order.status = LiveOrderStatus.SUBMITTED
            order.submitted_at = datetime.now(timezone.utc)
        except Exception as e:
            order.status = LiveOrderStatus.REJECTED
            order.reject_reason = str(e)
            logger.error("Order rejected by gateway: %s", e)

        order.updated_at = datetime.now(timezone.utc)
        self._orders[order.order_id] = order

        await self._publish_order_event(order)
        return order

    async def cancel_order(self, order_id: str) -> LiveOrder:
        order = self._orders.get(order_id)
        if order is None:
            raise ValueError(f"Order not found: {order_id}")
        if not order.is_active:
            raise ValueError(f"Order {order_id} is not cancellable (status={order.status})")

        gw = self.get_gateway(order.market)
        if not order.broker_order_id:
            raise ValueError("Order has no broker_order_id, cannot cancel")

        await gw.cancel_order(order.broker_order_id)
        order.status = LiveOrderStatus.CANCELLED
        order.updated_at = datetime.now(timezone.utc)
        await self._publish_order_event(order)
        return order

    # ── 查询接口 ──────────────────────────────────────────────

    def get_order(self, order_id: str) -> Optional[LiveOrder]:
        return self._orders.get(order_id)

    def list_orders(
        self,
        strategy_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[LiveOrder]:
        orders = list(self._orders.values())
        if strategy_id:
            orders = [o for o in orders if o.strategy_id == strategy_id]
        if status:
            orders = [o for o in orders if o.status.value == status]
        orders.sort(key=lambda o: o.created_at, reverse=True)
        return orders[:limit]

    async def get_account(self, market: str) -> dict:
        gw = self.get_gateway(market)
        account = await gw.get_account()
        return {
            "account_id": account.account_id,
            "currency": account.currency,
            "cash": account.cash,
            "buying_power": account.buying_power,
            "portfolio_value": account.portfolio_value,
        }

    async def get_positions(self, market: str) -> list[dict]:
        gw = self.get_gateway(market)
        positions = await gw.get_positions()
        return [
            {
                "symbol": p.symbol,
                "market": p.market,
                "qty": p.qty,
                "avg_cost": p.avg_cost,
                "current_price": p.current_price,
                "market_value": p.market_value,
                "unrealized_pnl": p.unrealized_pnl,
                "unrealized_pnl_pct": (
                    round((p.unrealized_pnl / (p.avg_cost * p.qty)) * 100, 4)
                    if p.unrealized_pnl and p.avg_cost and p.qty
                    else None
                ),
            }
            for p in positions
        ]

    # ── 风控前置检查 ──────────────────────────────────────────

    def _pre_trade_risk_check(self, order: LiveOrder) -> None:
        if order.qty <= 0:
            raise RiskViolation("Order qty must be positive")
        if order.qty > MAX_ORDER_QTY:
            raise RiskViolation(
                f"Order qty {order.qty} exceeds max allowed {MAX_ORDER_QTY}"
            )
        if order.order_type == LiveOrderType.LIMIT and not order.limit_price:
            raise RiskViolation("Limit order requires limit_price")
        if order.limit_price is not None and order.limit_price <= 0:
            raise RiskViolation("limit_price must be positive")

    # ── 状态轮询 ──────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """定期拉取活跃订单的最新状态。"""
        while self._running:
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                await self._sync_active_orders()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Order status poll failed")

    async def _sync_active_orders(self) -> None:
        active = [o for o in self._orders.values() if o.is_active]
        if not active:
            return

        for order in active:
            if not order.broker_order_id:
                continue
            try:
                gw = self.get_gateway(order.market)
                raw = await gw.get_order(order.broker_order_id)
                self._apply_broker_update(order, raw)
            except Exception:
                logger.debug("Failed to sync order %s", order.order_id)

    def _apply_broker_update(self, order: LiveOrder, raw: dict) -> None:
        old_status = order.status

        filled_qty = int(raw.get("filled_qty", 0))
        avg_price = raw.get("avg_fill_price")
        raw_status = str(raw.get("status", ""))

        # 映射状态（各网关已将状态转为统一字符串）
        status_map = {
            "submitted": LiveOrderStatus.SUBMITTED,
            "partial": LiveOrderStatus.PARTIAL,
            "filled": LiveOrderStatus.FILLED,
            "cancelled": LiveOrderStatus.CANCELLED,
            "rejected": LiveOrderStatus.REJECTED,
            "expired": LiveOrderStatus.EXPIRED,
        }
        new_status = status_map.get(raw_status, order.status)

        order.filled_qty = filled_qty
        order.avg_fill_price = float(avg_price) if avg_price else None
        order.status = new_status
        order.updated_at = datetime.now(timezone.utc)

        if new_status == LiveOrderStatus.FILLED and order.filled_at is None:
            order.filled_at = datetime.now(timezone.utc)

        if new_status != old_status:
            asyncio.create_task(self._publish_order_event(order))

    # ── 事件发布 ──────────────────────────────────────────────

    async def _publish_order_event(self, order: LiveOrder) -> None:
        if self._redis is None:
            return
        try:
            import json
            await self._redis.xadd(
                "orders:events",
                {"data": json.dumps(order.to_dict())},
                maxlen=10_000,
            )
        except Exception:
            logger.debug("Failed to publish order event to Redis")


# 全局单例（在 FastAPI lifespan 中初始化）
_manager: Optional[OrderManager] = None


def get_order_manager() -> OrderManager:
    if _manager is None:
        raise RuntimeError(
            "OrderManager not initialized. "
            "Call init_order_manager() during app startup."
        )
    return _manager


def init_order_manager(redis_client=None) -> OrderManager:
    global _manager
    _manager = OrderManager(redis_client=redis_client)
    return _manager


async def init_paper_order_manager(redis_client=None) -> OrderManager:
    """
    初始化纸面交易 OMS（无需真实券商配置）。
    为 US / HK / A 三个市场各注册一个 PaperGateway。
    """
    from app.gateway.paper_gateway import PaperGateway

    global _manager
    _manager = OrderManager(redis_client=redis_client)

    currency_map = {"US": ("USD", 1_000_000.0), "HK": ("HKD", 5_000_000.0), "A": ("CNY", 1_000_000.0)}
    for market, (currency, cash) in currency_map.items():
        gw = PaperGateway(market=market, initial_cash=cash, currency=currency)
        await gw.connect()
        _manager.register_gateway(market, gw)

    await _manager.start()
    logger.info("Paper trading OMS initialized for markets: US / HK / A")
    return _manager
