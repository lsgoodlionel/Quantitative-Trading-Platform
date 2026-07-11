"""订单 API 端点测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.oms.manager import OrderManager
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType
from app.api.v1.endpoints.orders import get_oms
from app.api.v1.endpoints.positions import _try_get_oms as get_oms_pos
from app.api.v1.endpoints.auth import get_current_user, UserInfo


def _admin_user() -> UserInfo:
    """测试用管理员身份，绕过 RBAC（require_role 依赖 get_current_user）。"""
    return UserInfo(id="test-admin", email="admin@test.local", role="admin")


def _make_order(**kwargs) -> LiveOrder:
    defaults = dict(
        symbol="AAPL",
        market="US",
        side=LiveOrderSide.BUY,
        qty=10,
        status=LiveOrderStatus.SUBMITTED,
        broker_order_id="broker-123",
    )
    defaults.update(kwargs)
    return LiveOrder(**defaults)


@pytest.fixture
def mock_oms() -> MagicMock:
    oms = MagicMock(spec=OrderManager)
    oms.submit_order = AsyncMock(return_value=_make_order())
    oms.cancel_order = AsyncMock(return_value=_make_order(status=LiveOrderStatus.CANCELLED))
    oms.get_order = MagicMock(return_value=_make_order())
    oms.list_orders = MagicMock(return_value=[_make_order()])
    oms.get_positions = AsyncMock(return_value=[
        {
            "symbol": "AAPL",
            "market": "US",
            "qty": 100,
            "avg_cost": 150.0,
            "current_price": 160.0,
            "market_value": 16_000.0,
            "unrealized_pnl": 1_000.0,
            "unrealized_pnl_pct": 6.67,
        }
    ])
    oms.get_account = AsyncMock(return_value={
        "account_id": "test-acc",
        "currency": "USD",
        "cash": 50_000.0,
        "buying_power": 100_000.0,
        "portfolio_value": 105_000.0,
    })
    return oms


@pytest.fixture(autouse=True)
def override_oms(mock_oms: MagicMock):
    # 写端点用 Depends(get_oms)，可用 dependency_overrides
    app.dependency_overrides[get_oms] = lambda: mock_oms
    # RBAC：require_role 依赖 get_current_user，覆盖为管理员绕过权限校验
    app.dependency_overrides[get_current_user] = _admin_user
    # 读端点（list_orders/positions/account）内部直接调用 _try_get_oms()（非 Depends），需 patch
    with patch("app.api.v1.endpoints.orders._try_get_oms", lambda: mock_oms), \
         patch("app.api.v1.endpoints.positions._try_get_oms", lambda: mock_oms):
        yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestSubmitOrder:
    @pytest.mark.asyncio
    async def test_submit_market_buy(self, client: AsyncClient, mock_oms: MagicMock) -> None:
        resp = await client.post("/api/v1/orders", json={
            "symbol": "AAPL",
            "market": "US",
            "side": "BUY",
            "qty": 100,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["symbol"] == "AAPL"
        assert body["status"] == "submitted"

    @pytest.mark.asyncio
    async def test_invalid_side_returns_400(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/orders", json={
            "symbol": "AAPL",
            "market": "US",
            "side": "INVALID",
            "qty": 10,
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_zero_qty_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/orders", json={
            "symbol": "AAPL",
            "market": "US",
            "side": "BUY",
            "qty": 0,
        })
        assert resp.status_code == 422


class TestListOrders:
    @pytest.mark.asyncio
    async def test_list_returns_orders(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/orders")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1

    @pytest.mark.asyncio
    async def test_list_with_strategy_filter(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        resp = await client.get("/api/v1/orders?strategy_id=s1")
        assert resp.status_code == 200
        mock_oms.list_orders.assert_called_once_with(
            strategy_id="s1", status=None, limit=100
        )


class TestGetOrder:
    @pytest.mark.asyncio
    async def test_get_existing_order(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/orders/some-id")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        mock_oms.get_order.return_value = None
        resp = await client.get("/api/v1/orders/nonexistent")
        assert resp.status_code == 404


class TestCancelOrder:
    @pytest.mark.asyncio
    async def test_cancel_order(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/orders/some-id/cancel")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"


class TestPositions:
    @pytest.mark.asyncio
    async def test_list_positions(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/positions")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_get_account(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/positions/account")
        assert resp.status_code == 200
        body = resp.json()
        assert body["cash"] == 50_000.0
        assert body["currency"] == "USD"
