"""OMS OrderManager 单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.gateway.base import TradingGateway, AccountInfo, BrokerPosition
from app.oms.manager import OrderManager, RiskViolation
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType


class MockGateway(TradingGateway):
    """测试用网关 mock。"""

    def __init__(self, should_fail: bool = False) -> None:
        self._connected = True
        self._should_fail = should_fail
        self.submitted_orders: list[LiveOrder] = []
        self.cancelled_ids: list[str] = []

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def submit_order(self, order: LiveOrder) -> str:
        if self._should_fail:
            raise RuntimeError("Gateway submit failed")
        self.submitted_orders.append(order)
        return f"broker-{order.order_id[:8]}"

    async def cancel_order(self, broker_order_id: str) -> None:
        if self._should_fail:
            raise RuntimeError("Gateway cancel failed")
        self.cancelled_ids.append(broker_order_id)

    async def get_order(self, broker_order_id: str) -> dict:
        return {"broker_order_id": broker_order_id, "status": "filled", "filled_qty": 10}

    async def get_open_orders(self) -> list[dict]:
        return []

    async def get_account(self) -> AccountInfo:
        return AccountInfo(
            account_id="test-account",
            currency="USD",
            cash=100_000.0,
            buying_power=200_000.0,
            portfolio_value=105_000.0,
        )

    async def get_positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(
                symbol="AAPL",
                market="US",
                qty=100,
                avg_cost=150.0,
                current_price=160.0,
                market_value=16_000.0,
                unrealized_pnl=1_000.0,
            )
        ]


@pytest.fixture
def oms() -> OrderManager:
    manager = OrderManager(redis_client=None)
    manager.register_gateway("US", MockGateway())
    manager.register_gateway("HK", MockGateway())
    return manager


@pytest.fixture
def failing_oms() -> OrderManager:
    manager = OrderManager(redis_client=None)
    manager.register_gateway("US", MockGateway(should_fail=True))
    return manager


class TestGatewayRegistration:
    def test_register_and_get_gateway(self, oms: OrderManager) -> None:
        gw = oms.get_gateway("US")
        assert gw is not None

    def test_get_unregistered_market_raises(self, oms: OrderManager) -> None:
        with pytest.raises(ValueError, match="No gateway"):
            oms.get_gateway("JP")


class TestSubmitOrder:
    @pytest.mark.asyncio
    async def test_buy_order_submitted(self, oms: OrderManager) -> None:
        order = await oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 100)
        assert order.status == LiveOrderStatus.SUBMITTED
        assert order.broker_order_id is not None
        assert order.broker_order_id.startswith("broker-")

    @pytest.mark.asyncio
    async def test_sell_order_submitted(self, oms: OrderManager) -> None:
        order = await oms.submit_order("AAPL", "US", LiveOrderSide.SELL, 50)
        assert order.status == LiveOrderStatus.SUBMITTED

    @pytest.mark.asyncio
    async def test_order_stored_in_manager(self, oms: OrderManager) -> None:
        order = await oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 10)
        retrieved = oms.get_order(order.order_id)
        assert retrieved is not None
        assert retrieved.order_id == order.order_id

    @pytest.mark.asyncio
    async def test_gateway_failure_marks_order_rejected(
        self, failing_oms: OrderManager
    ) -> None:
        order = await failing_oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 10)
        assert order.status == LiveOrderStatus.REJECTED
        assert order.reject_reason is not None

    @pytest.mark.asyncio
    async def test_disconnected_gateway_raises(self) -> None:
        gw = MockGateway()
        gw._connected = False
        manager = OrderManager(redis_client=None)
        manager.register_gateway("US", gw)
        with pytest.raises(RuntimeError, match="not connected"):
            await manager.submit_order("AAPL", "US", LiveOrderSide.BUY, 10)


class TestRiskCheck:
    @pytest.mark.asyncio
    async def test_zero_qty_raises(self, oms: OrderManager) -> None:
        with pytest.raises(RiskViolation, match="qty must be positive"):
            await oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 0)

    @pytest.mark.asyncio
    async def test_excessive_qty_raises(self, oms: OrderManager) -> None:
        with pytest.raises(RiskViolation, match="exceeds max"):
            await oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 200_000)

    @pytest.mark.asyncio
    async def test_limit_order_without_price_raises(self, oms: OrderManager) -> None:
        with pytest.raises(RiskViolation, match="requires limit_price"):
            await oms.submit_order(
                "AAPL", "US", LiveOrderSide.BUY, 10,
                order_type=LiveOrderType.LIMIT,
                limit_price=None,
            )

    @pytest.mark.asyncio
    async def test_negative_limit_price_raises(self, oms: OrderManager) -> None:
        with pytest.raises(RiskViolation, match="must be positive"):
            await oms.submit_order(
                "AAPL", "US", LiveOrderSide.BUY, 10,
                order_type=LiveOrderType.LIMIT,
                limit_price=-1.0,
            )


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_active_order(self, oms: OrderManager) -> None:
        order = await oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 10)
        cancelled = await oms.cancel_order(order.order_id)
        assert cancelled.status == LiveOrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order_raises(self, oms: OrderManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            await oms.cancel_order("nonexistent-id")


class TestListOrders:
    @pytest.mark.asyncio
    async def test_list_returns_all_orders(self, oms: OrderManager) -> None:
        await oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 10, strategy_id="s1")
        await oms.submit_order("MSFT", "US", LiveOrderSide.BUY, 5, strategy_id="s2")
        orders = oms.list_orders()
        assert len(orders) == 2

    @pytest.mark.asyncio
    async def test_list_filtered_by_strategy(self, oms: OrderManager) -> None:
        await oms.submit_order("AAPL", "US", LiveOrderSide.BUY, 10, strategy_id="s1")
        await oms.submit_order("MSFT", "US", LiveOrderSide.BUY, 5, strategy_id="s2")
        orders = oms.list_orders(strategy_id="s1")
        assert len(orders) == 1
        assert orders[0].symbol == "AAPL"


class TestAccountAndPositions:
    @pytest.mark.asyncio
    async def test_get_account(self, oms: OrderManager) -> None:
        account = await oms.get_account("US")
        assert account["cash"] == 100_000.0
        assert account["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_get_positions(self, oms: OrderManager) -> None:
        positions = await oms.get_positions("US")
        assert len(positions) == 1
        assert positions[0]["symbol"] == "AAPL"
        assert positions[0]["qty"] == 100
