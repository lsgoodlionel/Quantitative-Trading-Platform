"""
期货网关预留接口桩

后期绑定账户和服务器时，参考:
  refs/vnpy/vnpy/gateway/ctp/  — CTP 期货网关完整实现

启用方式:
  1. 设置环境变量 FUTURES_ENABLED=true
  2. 配置 CTP_BROKER_ID / CTP_INVESTOR_ID / CTP_PASSWORD / CTP_TD_ADDRESS / CTP_MD_ADDRESS
  3. 实现 CtpGateway(FuturesGatewayBase)

目前: 仅定义接口，不实例化任何连接。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class FuturesPosition:
    symbol: str
    exchange: str
    direction: str   # LONG / SHORT
    volume: int
    frozen: int
    price: float
    pnl: float


@dataclass
class FuturesOrderRequest:
    symbol: str
    exchange: str
    direction: str
    offset: str      # OPEN / CLOSE / CLOSE_TODAY
    order_type: str  # LIMIT / MARKET
    volume: int
    price: float


class FuturesGatewayBase(ABC):
    """期货网关抽象基类 — 参考 refs/vnpy/vnpy/gateway/__init__.py"""

    @abstractmethod
    async def connect(self, settings: dict[str, Any]) -> None: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    async def send_order(self, req: FuturesOrderRequest) -> str: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> None: ...

    @abstractmethod
    async def query_position(self) -> list[FuturesPosition]: ...

    @abstractmethod
    async def query_account(self) -> dict[str, float]: ...


class CtpGatewayStub(FuturesGatewayBase):
    """CTP 接口桩 — FUTURES_ENABLED=false 时不做任何实际连接"""

    def __init__(self) -> None:
        if settings.futures_enabled:
            logger.warning(
                "CTP gateway stub is active — real CTP implementation pending. "
                "Reference: refs/vnpy/vnpy/gateway/ctp/"
            )

    async def connect(self, settings: dict[str, Any]) -> None:
        raise NotImplementedError("CTP gateway not yet implemented. See refs/vnpy/vnpy/gateway/ctp/")

    async def disconnect(self) -> None:
        pass

    async def send_order(self, req: FuturesOrderRequest) -> str:
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> None:
        raise NotImplementedError

    async def query_position(self) -> list[FuturesPosition]:
        return []

    async def query_account(self) -> dict[str, float]:
        return {}
