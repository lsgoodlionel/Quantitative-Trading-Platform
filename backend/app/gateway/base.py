"""
交易网关抽象基类

参考: refs/vnpy/vnpy/gateway/__init__.py BaseGateway 设计
适配为 asyncio 原生接口（不使用 threading）。

每个具体网关（Alpaca / 富途）实现此接口，OMS 通过统一接口调度。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from app.oms.order import LiveOrder, LiveFill, LiveOrderSide, LiveOrderType


@dataclass
class AccountInfo:
    account_id: str
    currency: str
    cash: float
    buying_power: float
    portfolio_value: float
    day_trade_count: int = 0


@dataclass
class BrokerPosition:
    symbol: str
    market: str
    qty: int
    avg_cost: float
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None


class TradingGateway(ABC):
    """
    交易网关抽象接口。

    实现类负责:
    - 连接/断开券商 API
    - 提交/撤销订单
    - 查询账户、持仓、订单状态
    """

    @abstractmethod
    async def connect(self) -> None:
        """建立连接，初始化认证。"""

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接，释放资源。"""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """当前是否处于连接状态。"""

    @abstractmethod
    async def submit_order(self, order: LiveOrder) -> str:
        """
        发送订单到券商，返回券商原始订单 ID（broker_order_id）。
        提交失败时抛出异常。
        """

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> None:
        """
        撤销指定券商订单。
        撤销失败时抛出异常。
        """

    @abstractmethod
    async def get_order(self, broker_order_id: str) -> dict:
        """查询单个订单状态，返回原始响应字典。"""

    @abstractmethod
    async def get_open_orders(self) -> list[dict]:
        """查询所有当前挂单。"""

    @abstractmethod
    async def get_account(self) -> AccountInfo:
        """查询账户资金状态。"""

    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]:
        """查询当前持仓列表。"""
