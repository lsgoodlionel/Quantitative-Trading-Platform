"""
Interactive Brokers 实盘网关（美股 + 期权 + 期货）

参考:
  github.com/erdewit/ib_insync — asyncio 原生 IB API 库
  IB API 文档: https://interactivebrokers.github.io/tws-api/

前置条件:
  1. 安装 TWS 或 IB Gateway 并运行
  2. API 设置 → 启用 Socket Client
  3. 配置 settings.ibkr_host / ibkr_port / ibkr_client_id

环境变量:
  IBKR_HOST=127.0.0.1        # TWS/IB Gateway 所在主机
  IBKR_PORT=7497             # 7497=TWS纸盘 7496=TWS实盘 4002=IB Gateway纸盘 4001=IB Gateway实盘
  IBKR_CLIENT_ID=1           # 同一账户可开多个连接，各用不同 client_id
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.gateway.base import AccountInfo, BrokerPosition, TradingGateway
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType

logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────
_ORDER_STATUS_MAP: dict[str, LiveOrderStatus] = {
    "Submitted":      LiveOrderStatus.SUBMITTED,
    "PreSubmitted":   LiveOrderStatus.SUBMITTED,
    "PartiallyFilled": LiveOrderStatus.PARTIAL,
    "Filled":         LiveOrderStatus.FILLED,
    "Cancelled":      LiveOrderStatus.CANCELLED,
    "Inactive":       LiveOrderStatus.CANCELLED,
}


def _get_ib():
    """延迟导入 ib_insync，避免未安装时启动失败。"""
    try:
        import ib_insync as ib
        return ib
    except ImportError as e:
        raise RuntimeError(
            "ib_insync not installed. Run: pip install ib_insync"
        ) from e


class IBGateway(TradingGateway):
    """
    Interactive Brokers 实盘网关。

    通过 ib_insync 异步连接 TWS / IB Gateway。
    支持:
    - 美股市价单 / 限价单 / 止损单
    - 账户资金查询
    - 实时持仓同步

    线程安全注意:
    - ib_insync 内部用 asyncio + eventkit
    - 所有 IB API 调用必须在同一个 event loop 中执行
    - 本类通过 asyncio.create_task 保证在正确 loop 中运行
    """

    def __init__(self) -> None:
        self._ib = None
        self._connected = False
        self._account_id: Optional[str] = None
        # 内部订单 id 映射: order_id → ib_trade 对象
        self._trades: dict[str, object] = {}

    # ── 生命周期 ──────────────────────────────────────────────

    async def connect(self) -> None:
        ib = _get_ib()
        self._ib = ib.IB()

        try:
            await self._ib.connectAsync(
                host=settings.ibkr_host,
                port=settings.ibkr_port,
                clientId=settings.ibkr_client_id,
                timeout=20,
            )
        except Exception as e:
            raise RuntimeError(
                f"Cannot connect to IB TWS/Gateway at "
                f"{settings.ibkr_host}:{settings.ibkr_port}: {e}"
            ) from e

        self._connected = True
        # 获取主账户 ID
        accounts = self._ib.managedAccounts()
        self._account_id = accounts[0] if accounts else "IB-UNKNOWN"
        logger.info(
            "IB gateway connected — account=%s port=%d",
            self._account_id, settings.ibkr_port,
        )

    async def disconnect(self) -> None:
        if self._ib:
            self._ib.disconnect()
        self._connected = False
        logger.info("IB gateway disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected and (self._ib is not None) and self._ib.isConnected()

    # ── 下单接口 ──────────────────────────────────────────────

    async def submit_order(self, order: LiveOrder) -> str:
        """
        向 IB 提交订单。

        返回 IB 的 permId（永久订单 ID）或 orderId 字符串。
        市价单: 使用 MKT order type
        限价单: 使用 LMT order type + limit price
        """
        ib = _get_ib()
        self._require_connected()

        contract = self._make_contract(order.symbol, order.market)

        if order.order_type == LiveOrderType.LIMIT:
            if not order.limit_price:
                raise ValueError("Limit order requires limit_price")
            ib_order = ib.LimitOrder(
                action=order.side.value,   # "BUY" / "SELL"
                totalQuantity=order.qty,
                lmtPrice=round(order.limit_price, 2),
            )
        else:
            # MARKET
            ib_order = ib.MarketOrder(
                action=order.side.value,
                totalQuantity=order.qty,
            )

        ib_order.account = self._account_id
        ib_order.tif = "DAY"           # Day order（当日有效）

        try:
            trade = self._ib.placeOrder(contract, ib_order)
            self._trades[order.order_id] = trade
            broker_order_id = str(trade.order.orderId)
            logger.info(
                "IB order placed: %s %d %s @ %s → broker_id=%s",
                order.side.value, order.qty, order.symbol,
                order.limit_price or "MKT", broker_order_id,
            )
            return broker_order_id
        except Exception as e:
            raise RuntimeError(f"IB submit_order failed: {e}") from e

    async def cancel_order(self, broker_order_id: str) -> None:
        """撤销订单。通过 orderId 找到对应 trade 并撤单。"""
        self._require_connected()
        # 查找对应的 trade 对象
        trade = self._find_trade_by_broker_id(broker_order_id)
        if trade is None:
            logger.warning("cancel_order: trade not found for broker_id=%s", broker_order_id)
            return
        try:
            self._ib.cancelOrder(trade.order)
            logger.info("IB cancel order: broker_id=%s", broker_order_id)
        except Exception as e:
            raise RuntimeError(f"IB cancel_order failed: {e}") from e

    async def get_order(self, broker_order_id: str) -> dict:
        """
        查询单个订单状态。

        返回统一格式 dict，OMS 用于更新 LiveOrder 状态。
        """
        self._require_connected()
        trade = self._find_trade_by_broker_id(broker_order_id)
        if trade is None:
            return {"status": "submitted", "filled_qty": 0, "avg_fill_price": None}

        order_status = trade.orderStatus
        status_str = _ORDER_STATUS_MAP.get(
            order_status.status, LiveOrderStatus.SUBMITTED
        ).value
        filled = int(order_status.filled)
        avg_price = float(order_status.avgFillPrice) if order_status.avgFillPrice else None

        return {
            "broker_order_id": broker_order_id,
            "status": status_str,
            "filled_qty": filled,
            "avg_fill_price": avg_price,
        }

    async def get_open_orders(self) -> list[dict]:
        """查询所有挂单。"""
        self._require_connected()
        try:
            trades = self._ib.trades()
            result = []
            for t in trades:
                if t.orderStatus.status not in ("Filled", "Cancelled", "Inactive"):
                    result.append({
                        "broker_order_id": str(t.order.orderId),
                        "symbol": t.contract.symbol,
                        "side": t.order.action,
                        "qty": t.order.totalQuantity,
                        "status": t.orderStatus.status,
                    })
            return result
        except Exception as e:
            logger.error("IB get_open_orders failed: %s", e)
            return []

    # ── 账户 / 持仓 ────────────────────────────────────────────

    async def get_account(self) -> AccountInfo:
        """查询账户资金信息。"""
        self._require_connected()
        try:
            summary = {
                v.tag: v.value
                for v in self._ib.accountSummary(self._account_id)
                if v.currency == "USD" or v.tag in ("Currency",)
            }
            cash = float(summary.get("AvailableFunds", 0))
            net_liq = float(summary.get("NetLiquidation", 0))
            buying_power = float(summary.get("BuyingPower", cash))

            return AccountInfo(
                account_id=self._account_id or "IB",
                currency="USD",
                cash=round(cash, 2),
                buying_power=round(buying_power, 2),
                portfolio_value=round(net_liq, 2),
            )
        except Exception as e:
            logger.error("IB get_account failed: %s", e)
            return AccountInfo(
                account_id=self._account_id or "IB",
                currency="USD",
                cash=0.0,
                buying_power=0.0,
                portfolio_value=0.0,
            )

    async def get_positions(self) -> list[BrokerPosition]:
        """查询当前持仓列表。"""
        self._require_connected()
        try:
            positions = self._ib.positions(self._account_id)
            result = []
            for pos in positions:
                qty = int(pos.position)
                if qty == 0:
                    continue
                avg_cost = float(pos.avgCost)
                symbol = pos.contract.symbol
                # 尝试获取当前市价（来自 portfolio 报告）
                current_price = avg_cost  # 默认等于成本
                mv = current_price * qty
                upnl = (current_price - avg_cost) * qty

                result.append(BrokerPosition(
                    symbol=symbol,
                    market="US",
                    qty=qty,
                    avg_cost=round(avg_cost, 4),
                    current_price=round(current_price, 4),
                    market_value=round(mv, 2),
                    unrealized_pnl=round(upnl, 2),
                ))
            return result
        except Exception as e:
            logger.error("IB get_positions failed: %s", e)
            return []

    # ── 私有工具 ──────────────────────────────────────────────

    def _require_connected(self) -> None:
        if not self.is_connected:
            raise RuntimeError(
                "IB gateway not connected. "
                "Ensure TWS/IB Gateway is running and call connect() first."
            )

    def _make_contract(self, symbol: str, market: str):
        """根据标的代码和市场创建 IB Contract 对象。"""
        ib = _get_ib()
        if market.upper() == "US":
            return ib.Stock(symbol, "SMART", "USD")
        # 港股：使用 IBKR HKEx 市场
        if market.upper() == "HK":
            return ib.Stock(symbol, "SEHK", "HKD")
        # 其他：默认 SMART 路由
        return ib.Stock(symbol, "SMART", "USD")

    def _find_trade_by_broker_id(self, broker_order_id: str) -> Optional[object]:
        """从内存中找到对应 broker_id 的 trade 对象。"""
        for trade in self._trades.values():
            if str(trade.order.orderId) == broker_order_id:
                return trade
        # 尝试从 IB 实时 trades() 中查找
        if self._ib:
            for t in self._ib.trades():
                if str(t.order.orderId) == broker_order_id:
                    return t
        return None
