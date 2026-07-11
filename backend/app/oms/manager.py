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

from app.core.audit import AuditAction, audit_log
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

    def __init__(self, redis_client=None, protection_manager=None) -> None:
        self._gateways: dict[str, TradingGateway] = {}
        self._orders: dict[str, LiveOrder] = {}   # order_id → LiveOrder
        self._redis = redis_client
        self._protections = protection_manager   # 可选：ProtectionManager，None 时跳过防护
        self._poll_task: Optional[asyncio.Task] = None
        self._running = False

    def set_protection_manager(self, protection_manager) -> None:
        """注入动态防护管理器（None 时跳过防护）。"""
        self._protections = protection_manager

    # ── Dry-Run / Live 一致性（E4）──────────────────────────────
    # 模拟盘（dry_run）与实盘共享同一条执行路径：相同订单生命周期、
    # 相同事件、相同风控/防护，唯一差别是路由到的网关为 Paper。
    # 这些方法为「加钩子」式扩展，不改动 __init__ 签名。

    def set_dry_run(self, value: bool) -> None:
        """显式标记全局模拟盘模式（强制所有订单 paper_mode=True）。"""
        self._dry_run = bool(value)

    @property
    def is_dry_run(self) -> bool:
        return getattr(self, "_dry_run", False)

    def _stamp_paper_mode(self, order: "LiveOrder") -> None:
        """
        标记订单是否为模拟盘：显式 dry_run 或路由网关为 PaperGateway 时为 True。

        使 paper 与 live 订单走完全相同的 OMS 流程，仅凭该标志区分来源，
        让前端/审计能一致地识别模拟盘成交。
        """
        if self.is_dry_run:
            order.paper_mode = True
            return
        gw = self._gateways.get(order.market.upper())
        order.paper_mode = type(gw).__name__ == "PaperGateway" if gw is not None else False

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
        self._stamp_paper_mode(order)

        self._pre_trade_risk_check(order)

        # 动态防护：仅对入场（BUY）订单 gate；平仓/卖出永不阻断（不困住持仓）
        if self._protections is not None and side == LiveOrderSide.BUY:
            lock = self._protections.check_entry(symbol, market)
            if lock is not None:
                order.status = LiveOrderStatus.REJECTED
                order.reject_reason = (
                    f"[PROTECTION:{lock.protection_type.value}] {lock.reason}"
                )
                order.updated_at = datetime.now(timezone.utc)
                self._orders[order.order_id] = order
                await self._publish_order_event(order)
                self._notify_protection(lock, order)
                return order

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
            self._notify_order_reject(order)

        order.updated_at = datetime.now(timezone.utc)
        self._orders[order.order_id] = order

        await self._publish_order_event(order)
        await self._audit_order(AuditAction.ORDER_SUBMIT, order)
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
        await self._audit_order(AuditAction.ORDER_CANCEL, order)
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
            if new_status in (LiveOrderStatus.FILLED, LiveOrderStatus.PARTIAL):
                self._notify_fill(order)
                # 成交后重新评估防护，使新触发的锁在下一次入场前生效
                if self._protections is not None:
                    try:
                        self._protections.evaluate()
                    except Exception:
                        logger.debug("Protection evaluate on fill failed")

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

    # ── 审计留痕（fire-and-forget，不阻塞热路径） ──────────────

    async def _audit_order(self, action: str, order: LiveOrder) -> None:
        """下单 / 撤单成功后写审计留痕（audit_log 内部永不抛出）。"""
        await audit_log(
            action,
            actor=order.strategy_id or "system",
            detail={
                "order_id": order.order_id,
                "symbol": order.symbol,
                "market": order.market,
                "side": order.side.value,
                "qty": order.qty,
                "order_type": order.order_type.value,
                "status": order.status.value,
                "paper_mode": getattr(order, "paper_mode", False),
            },
            redis=self._redis,
        )

    # ── 通知分发（fire-and-forget，不阻塞热路径） ──────────────

    def _notify_protection(self, lock, order: LiveOrder) -> None:
        try:
            from app.tasks.notify import emit_event

            emit_event(
                "protection",
                f"防护熔断阻止入场 · {order.symbol}",
                symbol=order.symbol,
                market=order.market,
                payload={
                    "protection_type": lock.protection_type.value,
                    "scope": lock.scope.value,
                    "reason": lock.reason,
                    "until": lock.until.isoformat(),
                },
            )
        except Exception:
            logger.debug("Failed to dispatch protection notification")

    def _notify_order_reject(self, order: LiveOrder) -> None:
        try:
            from app.tasks.notify import emit_event

            emit_event(
                "order_reject",
                f"订单被拒 · {order.symbol}",
                symbol=order.symbol,
                market=order.market,
                payload={
                    "order_id": order.order_id,
                    "reason": order.reject_reason or "unknown",
                },
            )
        except Exception:
            logger.debug("Failed to dispatch order-reject notification")

    def _notify_fill(self, order: LiveOrder) -> None:
        try:
            from app.tasks.notify import emit_event

            emit_event(
                "trade_fill",
                f"订单成交 · {order.symbol}",
                symbol=order.symbol,
                market=order.market,
                payload={
                    "order_id": order.order_id,
                    "side": order.side.value,
                    "filled_qty": order.filled_qty,
                    "avg_fill_price": order.avg_fill_price,
                },
            )
        except Exception:
            logger.debug("Failed to dispatch fill notification")


class OrderManagerTradeSource:
    """
    TradeSource 适配器：将 OrderManager 内存订单簿映射为 TradeRecord。

    平仓语义：现货多头下，SELL 成交 = 平仓（一笔已闭合交易），BUY 成交 = 开仓
    （尚未闭合，不计入）。exit_reason 依据订单标注启发式推断，使 StoplossGuard
    仍能识别止损平仓。

    Wave-1D 限制：LiveOrder 不含逐笔盈亏，profit_ratio / profit_abs 暂为 0。
    后续接入 DB orders/fills 表后替换为真实盈亏与显式 exit_reason。
    """

    def __init__(self, manager: "OrderManager") -> None:
        self._manager = manager

    def get_closed_trades(self, symbol, since):
        from app.oms.protections.base import TradeRecord

        out = []
        for order in self._manager._orders.values():
            if order.status != LiveOrderStatus.FILLED:
                continue
            # 仅 SELL 成交代表平仓；BUY 为开仓，不是已闭合交易
            if order.side != LiveOrderSide.SELL:
                continue
            if symbol is not None and order.symbol != symbol:
                continue
            close_date = order.filled_at or order.updated_at
            if close_date is None:
                continue
            # since 为带时区 UTC；close_date 为 naive UTC，比较时补 tzinfo
            cd = close_date if close_date.tzinfo else close_date.replace(tzinfo=timezone.utc)
            if cd < since:
                continue
            out.append(
                TradeRecord(
                    symbol=order.symbol,
                    market=order.market,
                    side=order.side.value,
                    close_date=cd,
                    profit_ratio=0.0,
                    profit_abs=0.0,
                    exit_reason=_infer_exit_reason(order),
                )
            )
        return out


def _infer_exit_reason(order: LiveOrder) -> str:
    """
    从平仓订单的标注启发式推断 exit_reason。

    LiveOrder 目前无显式 exit_reason 字段，退而从 strategy_id 约定的标记里推断，
    使止损/止盈平仓可被 StoplossGuard 等防护识别。无匹配时回退 'signal'。
    """
    hint = (order.strategy_id or "").lower()
    if "stop_loss" in hint or "stoploss" in hint or "stop" in hint:
        return "stop_loss"
    if "take_profit" in hint or "takeprofit" in hint or "take-profit" in hint:
        return "take_profit"
    return "signal"


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


async def _attach_protections(manager: OrderManager, redis_client=None) -> None:
    """从 Redis 加载防护配置与活跃锁，初始化 ProtectionManager 并注入 OMS。"""
    from app.oms.protections.manager import init_protection_manager
    from app.oms.protections.store import (
        load_config as load_protections_config,
        load_locks as load_protection_locks,
    )

    config = None
    persisted_locks = []
    if redis_client is not None:
        try:
            config = await load_protections_config(redis_client)
        except Exception:
            logger.warning("Failed to load protections config, using defaults")
        try:
            persisted_locks = await load_protection_locks(redis_client)
        except Exception:
            logger.warning("Failed to load persisted protection locks")

    trade_source = OrderManagerTradeSource(manager)
    prot_manager = init_protection_manager(
        config=config, trade_source=trade_source, redis_client=redis_client
    )
    if persisted_locks:
        prot_manager.restore_locks(persisted_locks)
        logger.info("Restored %d persisted protection lock(s)", len(persisted_locks))
    manager.set_protection_manager(prot_manager)
    logger.info("Dynamic protections attached to OMS")


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
    await _attach_protections(_manager, redis_client)
    logger.info("Paper trading OMS initialized for markets: US / HK / A")
    return _manager


async def init_hybrid_order_manager(redis_client=None) -> OrderManager:
    """
    智能混合 OMS 初始化。

    自动检测 Redis 中的 Alpaca 配置：
    - 若配置了 Alpaca → US 市场使用 AlpacaGateway（Paper 或 Live）
    - 若未配置 → US 市场回退到 PaperGateway
    - HK / A 市场始终使用 PaperGateway（待接入富途等网关）

    Returns:
        OrderManager: 已启动的订单管理器，同时设置全局 _manager
    """
    from app.gateway.paper_gateway import PaperGateway

    global _manager
    _manager = OrderManager(redis_client=redis_client)

    # ── 检测 Alpaca 配置 ──────────────────────────────────────
    alpaca_cfg: dict[str, str] = {}
    if redis_client:
        try:
            raw = await redis_client.hgetall("broker_config:alpaca")
            if raw:
                alpaca_cfg = {
                    k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in raw.items()
                }
        except Exception as e:
            logger.warning("Failed to read Alpaca config from Redis: %s", e)

    # ── US 市场 ───────────────────────────────────────────────
    if alpaca_cfg.get("api_key") and alpaca_cfg.get("api_secret"):
        paper_mode = alpaca_cfg.get("paper_mode", "true").lower() == "true"
        try:
            from app.gateway.alpaca_gateway import AlpacaGateway
            gw_us = AlpacaGateway(
                api_key=alpaca_cfg["api_key"],
                secret_key=alpaca_cfg["api_secret"],
                paper=paper_mode,
            )
            await gw_us.connect()
            _manager.register_gateway("US", gw_us)
            mode_label = "Paper (Alpaca)" if paper_mode else "Live (Alpaca ⚠ 真实资金)"
            logger.info("US market: AlpacaGateway connected (%s)", mode_label)
        except Exception as e:
            logger.warning("AlpacaGateway connection failed, falling back to PaperGateway: %s", e)
            gw_us = PaperGateway(market="US", initial_cash=1_000_000.0, currency="USD")
            await gw_us.connect()
            _manager.register_gateway("US", gw_us)
    else:
        gw_us = PaperGateway(market="US", initial_cash=1_000_000.0, currency="USD")
        await gw_us.connect()
        _manager.register_gateway("US", gw_us)
        logger.info("US market: PaperGateway (Alpaca not configured)")

    # ── HK / A 市场（暂用 PaperGateway）─────────────────────
    for market, currency, cash in [("HK", "HKD", 5_000_000.0), ("A", "CNY", 1_000_000.0)]:
        gw = PaperGateway(market=market, initial_cash=cash, currency=currency)
        await gw.connect()
        _manager.register_gateway(market, gw)

    await _manager.start()
    await _attach_protections(_manager, redis_client)
    logger.info("Hybrid OMS initialized")
    return _manager
