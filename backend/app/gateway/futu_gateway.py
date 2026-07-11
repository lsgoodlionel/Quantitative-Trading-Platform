"""
富途 OpenAPI 实盘网关（港股 + 美股）

参考:
  refs/futu-api-doc/  — 富途 OpenAPI 文档
  futu-api SDK 同步调用，包裹在 run_in_executor

连接前提: 本地运行 FuTuOpenD（富途牛牛客户端）
  - 端口: settings.futu_port (默认 11111)
  - 交易环境: settings.futu_trade_env (SIMULATE / REAL)
  - 解锁密码: settings.futu_unlock_pwd（实盘必填）
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from app.core.config import settings
from app.gateway.base import TradingGateway, AccountInfo, BrokerPosition
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType

logger = logging.getLogger(__name__)


def _to_futu_symbol(symbol: str, market: str) -> str:
    """AAPL + US → US.AAPL; 00700 + HK → HK.00700"""
    if market.upper() == "HK":
        return f"HK.{symbol.zfill(5)}"
    return f"US.{symbol}"


class FutuGateway(TradingGateway):
    """
    富途 OpenAPI 实盘网关。

    特性:
    - 支持港股、美股交易
    - 沙盒（SIMULATE）和实盘（REAL）切换
    - 解锁密码保护
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        trade_env: str | None = None,
        unlock_pwd: str | None = None,
    ) -> None:
        # 可通过构造函数覆盖（优先于 settings），仿 AlpacaGateway 接入模式
        self._host = host or settings.futu_host
        self._port = int(port) if port is not None else settings.futu_port
        self._trade_env = (trade_env or settings.futu_trade_env).upper()
        self._unlock_pwd = unlock_pwd if unlock_pwd is not None else settings.futu_unlock_pwd
        self._trade_ctx = None
        self._connected = False
        self._account_id: Optional[str] = None

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()

        def _init():
            try:
                import futu as ft
            except ImportError as e:
                raise RuntimeError(
                    "futu-api not installed. Run: pip install futu-api"
                ) from e

            env = (
                ft.TrdEnv.SIMULATE
                if self._trade_env == "SIMULATE"
                else ft.TrdEnv.REAL
            )
            ctx = ft.OpenSecTradeContext(
                host=self._host,
                port=self._port,
                filter_trdmarket=ft.TrdMarket.HK,  # 默认港股，可扩展
            )
            # 实盘模式需要解锁
            if env == ft.TrdEnv.REAL and self._unlock_pwd:
                ret, data = ctx.unlock_trade(password=self._unlock_pwd)
                if ret != ft.RET_OK:
                    ctx.close()
                    raise RuntimeError(f"Futu unlock failed: {data}")

            return ctx, env

        self._trade_ctx, self._env = await loop.run_in_executor(None, _init)
        self._connected = True
        logger.info(
            "Futu gateway connected (env=%s, host=%s:%d)",
            self._trade_env, self._host, self._port,
        )

    async def disconnect(self) -> None:
        if self._trade_ctx:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._trade_ctx.close)
            self._trade_ctx = None
        self._connected = False
        logger.info("Futu gateway disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _require_ctx(self):
        if self._trade_ctx is None:
            raise RuntimeError("Futu gateway not connected. Call connect() first.")
        return self._trade_ctx

    async def submit_order(self, order: LiveOrder) -> str:
        import futu as ft
        ctx = self._require_ctx()
        loop = asyncio.get_event_loop()

        code = _to_futu_symbol(order.symbol, order.market)
        trd_side = ft.TrdSide.BUY if order.side == LiveOrderSide.BUY else ft.TrdSide.SELL

        if order.order_type == LiveOrderType.LIMIT and order.limit_price:
            order_type = ft.OrderType.NORMAL
            price = order.limit_price
        else:
            order_type = ft.OrderType.MARKET
            price = 0.0

        def _submit():
            ret, data = ctx.place_order(
                price=price,
                qty=order.qty,
                code=code,
                trd_side=trd_side,
                order_type=order_type,
                trd_env=self._env,
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu place_order failed: {data}")
            return str(data["order_id"].iloc[0])

        try:
            broker_id = await loop.run_in_executor(None, _submit)
            logger.info(
                "Order submitted to Futu: %s %s x%d → broker_id=%s",
                order.side.value, order.symbol, order.qty, broker_id,
            )
            return broker_id
        except Exception as e:
            logger.error("Futu order submission failed: %s", e)
            raise

    async def cancel_order(self, broker_order_id: str) -> None:
        import futu as ft
        ctx = self._require_ctx()
        loop = asyncio.get_event_loop()

        def _cancel():
            ret, data = ctx.modify_order(
                modify_order_op=ft.ModifyOrderOp.CANCEL,
                order_id=int(broker_order_id),
                qty=0,
                price=0,
                trd_env=self._env,
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu cancel_order failed: {data}")

        try:
            await loop.run_in_executor(None, _cancel)
            logger.info("Order cancelled: %s", broker_order_id)
        except Exception as e:
            logger.error("Futu cancel failed for %s: %s", broker_order_id, e)
            raise

    async def get_order(self, broker_order_id: str) -> dict:
        import futu as ft
        ctx = self._require_ctx()
        loop = asyncio.get_event_loop()

        def _get():
            ret, data = ctx.order_list_query(
                order_id=int(broker_order_id),
                trd_env=self._env,
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu order_list_query failed: {data}")
            if data.empty:
                return {}
            row = data.iloc[0]
            return _futu_row_to_dict(row)

        return await loop.run_in_executor(None, _get)

    async def get_open_orders(self) -> list[dict]:
        import futu as ft
        ctx = self._require_ctx()
        loop = asyncio.get_event_loop()

        def _get():
            ret, data = ctx.order_list_query(
                status_filter_list=[
                    ft.OrderStatus.SUBMITTING,
                    ft.OrderStatus.SUBMITTED,
                    ft.OrderStatus.FILLED_PART,
                ],
                trd_env=self._env,
            )
            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu open orders query failed: {data}")
            return [_futu_row_to_dict(data.iloc[i]) for i in range(len(data))]

        return await loop.run_in_executor(None, _get)

    async def get_account(self) -> AccountInfo:
        import futu as ft
        ctx = self._require_ctx()
        loop = asyncio.get_event_loop()

        def _get():
            ret, data = ctx.accinfo_query(trd_env=self._env)
            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu accinfo_query failed: {data}")
            row = data.iloc[0]
            return AccountInfo(
                account_id=str(row.get("acc_id", "futu")),
                currency=str(row.get("currency", "HKD")),
                cash=float(row.get("cash", 0)),
                buying_power=float(row.get("power", 0)),
                portfolio_value=float(row.get("total_assets", 0)),
            )

        return await loop.run_in_executor(None, _get)

    async def get_positions(self) -> list[BrokerPosition]:
        import futu as ft
        ctx = self._require_ctx()
        loop = asyncio.get_event_loop()

        def _get():
            ret, data = ctx.position_list_query(trd_env=self._env)
            if ret != ft.RET_OK:
                raise RuntimeError(f"Futu position_list_query failed: {data}")
            result = []
            for i in range(len(data)):
                row = data.iloc[i]
                code: str = str(row.get("code", ""))
                # HK.00700 → 00700
                symbol = code.split(".")[-1] if "." in code else code
                market = code.split(".")[0] if "." in code else "HK"
                result.append(
                    BrokerPosition(
                        symbol=symbol,
                        market=market,
                        qty=int(row.get("qty", 0)),
                        avg_cost=float(row.get("cost_price", 0)),
                        current_price=float(row.get("price", 0)) or None,
                        market_value=float(row.get("market_val", 0)) or None,
                        unrealized_pnl=float(row.get("pl_val", 0)) or None,
                    )
                )
            return result

        return await loop.run_in_executor(None, _get)


# 富途订单状态 → OMS 统一状态字符串（OrderManager._apply_broker_update 识别小写口径）
_FUTU_STATUS_MAP: dict[str, str] = {
    "WAITING_SUBMIT": "submitted",
    "SUBMITTING": "submitted",
    "SUBMITTED": "submitted",
    "FILLED_PART": "partial",
    "FILLED_ALL": "filled",
    "CANCELLED_PART": "cancelled",
    "CANCELLED_ALL": "cancelled",
    "CANCELLING_PART": "submitted",
    "CANCELLING_ALL": "submitted",
    "FAILED": "rejected",
    "SUBMIT_FAILED": "rejected",
    "DISABLED": "cancelled",
    "DELETED": "cancelled",
    "TIMEOUT": "expired",
}


def _map_futu_status(raw_status: str) -> str:
    """富途原始状态 → OMS 统一状态；含枚举前缀（OrderStatus.FILLED_ALL）时取尾段。"""
    token = raw_status.split(".")[-1].upper() if raw_status else ""
    return _FUTU_STATUS_MAP.get(token, "submitted")


def _futu_row_to_dict(row) -> dict:
    return {
        "broker_order_id": str(row.get("order_id", "")),
        "symbol": str(row.get("code", "")).split(".")[-1],
        "side": str(row.get("trd_side", "")),
        "qty": int(row.get("qty", 0)),
        "filled_qty": int(row.get("dealt_qty", 0)),
        "avg_fill_price": float(row.get("dealt_avg_price", 0)) or None,
        "status": _map_futu_status(str(row.get("order_status", ""))),
        "created_at": str(row.get("create_time", "")),
    }
