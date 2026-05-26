"""Alpaca 网关单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import pytest

from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType
from app.gateway.alpaca_gateway import AlpacaGateway, _map_alpaca_status
from app.gateway.base import AccountInfo, BrokerPosition


class TestAlpacaStatusMapping:
    def test_new_maps_to_submitted(self) -> None:
        assert _map_alpaca_status("new") == LiveOrderStatus.SUBMITTED

    def test_filled_maps_to_filled(self) -> None:
        assert _map_alpaca_status("filled") == LiveOrderStatus.FILLED

    def test_canceled_maps_to_cancelled(self) -> None:
        assert _map_alpaca_status("canceled") == LiveOrderStatus.CANCELLED

    def test_partially_filled_maps_to_partial(self) -> None:
        assert _map_alpaca_status("partially_filled") == LiveOrderStatus.PARTIAL

    def test_rejected_maps_to_rejected(self) -> None:
        assert _map_alpaca_status("rejected") == LiveOrderStatus.REJECTED

    def test_unknown_status_maps_to_submitted(self) -> None:
        assert _map_alpaca_status("unknown_status") == LiveOrderStatus.SUBMITTED

    def test_case_insensitive(self) -> None:
        assert _map_alpaca_status("FILLED") == LiveOrderStatus.FILLED


class TestAlpacaGatewayInit:
    def test_not_connected_initially(self) -> None:
        gw = AlpacaGateway()
        assert not gw.is_connected

    def test_require_client_raises_when_not_connected(self) -> None:
        gw = AlpacaGateway()
        with pytest.raises(RuntimeError, match="not connected"):
            gw._require_client()

    def test_require_client_returns_client_when_connected(self) -> None:
        gw = AlpacaGateway()
        mock_client = MagicMock()
        gw._client = mock_client
        gw._connected = True
        assert gw._require_client() is mock_client


class TestAlpacaSubmitOrderErrors:
    @pytest.mark.asyncio
    async def test_submit_raises_when_not_connected(self) -> None:
        gw = AlpacaGateway()
        order = LiveOrder(
            symbol="AAPL",
            market="US",
            side=LiveOrderSide.BUY,
            qty=10,
        )
        with pytest.raises(RuntimeError, match="not connected"):
            await gw.submit_order(order)

    @pytest.mark.asyncio
    async def test_submit_market_buy_calls_executor(self) -> None:
        """验证 submit_order 在有 client 时能通过 executor 调用到 SDK。"""
        gw = AlpacaGateway()
        mock_result = MagicMock()
        mock_result.id = "broker-abc-123"

        mock_client = MagicMock()
        mock_client.submit_order.return_value = mock_result
        gw._client = mock_client
        gw._connected = True

        # 用 side_effect 让 run_in_executor 同步执行传入的函数
        async def fake_executor(_, func, *args):
            if callable(func):
                return func()
            return func

        with patch("asyncio.get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.run_in_executor = fake_executor
            mock_get_loop.return_value = mock_loop

            # alpaca-py import mock
            with patch.dict("sys.modules", {
                "alpaca": MagicMock(),
                "alpaca.trading": MagicMock(),
                "alpaca.trading.requests": MagicMock(),
                "alpaca.trading.enums": MagicMock(),
            }):
                import alpaca.trading.enums as enums_mock
                enums_mock.OrderSide = MagicMock()
                enums_mock.OrderSide.BUY = "buy"
                enums_mock.TimeInForce = MagicMock()
                enums_mock.TimeInForce.DAY = "day"

                import alpaca.trading.requests as req_mock
                req_mock.MarketOrderRequest = MagicMock(return_value=MagicMock())

                broker_id = await gw.submit_order(
                    LiveOrder(symbol="AAPL", market="US", side=LiveOrderSide.BUY, qty=100)
                )
                assert broker_id == "broker-abc-123"


class TestAlpacaGatewayGetAccount:
    @pytest.mark.asyncio
    async def test_get_account_raises_when_not_connected(self) -> None:
        gw = AlpacaGateway()
        with pytest.raises(RuntimeError, match="not connected"):
            await gw.get_account()

    @pytest.mark.asyncio
    async def test_get_account_parses_alpaca_response(self) -> None:
        gw = AlpacaGateway()

        # Mock SDK account response
        mock_acc = MagicMock()
        mock_acc.id = "acc-abc"
        mock_acc.currency = "USD"
        mock_acc.cash = "50000.00"
        mock_acc.buying_power = "100000.00"
        mock_acc.portfolio_value = "105000.00"
        mock_acc.daytrade_count = 2

        mock_client = MagicMock()
        mock_client.get_account.return_value = mock_acc
        gw._client = mock_client
        gw._connected = True

        async def fake_executor(_, func, *args):
            return func()

        with patch("asyncio.get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.run_in_executor = fake_executor
            mock_get_loop.return_value = mock_loop

            account = await gw.get_account()

        assert account.account_id == "acc-abc"
        assert account.currency == "USD"
        assert account.cash == 50_000.0
        assert account.buying_power == 100_000.0
        assert account.day_trade_count == 2


class TestAlpacaGatewayGetPositions:
    @pytest.mark.asyncio
    async def test_get_positions_parses_response(self) -> None:
        gw = AlpacaGateway()

        mock_pos = MagicMock()
        mock_pos.symbol = "AAPL"
        mock_pos.qty = "100"
        mock_pos.avg_entry_price = "150.00"
        mock_pos.current_price = "160.00"
        mock_pos.market_value = "16000.00"
        mock_pos.unrealized_pl = "1000.00"

        mock_client = MagicMock()
        mock_client.get_all_positions.return_value = [mock_pos]
        gw._client = mock_client
        gw._connected = True

        async def fake_executor(_, func, *args):
            return func()

        with patch("asyncio.get_event_loop") as mock_get_loop:
            mock_loop = MagicMock()
            mock_loop.run_in_executor = fake_executor
            mock_get_loop.return_value = mock_loop

            positions = await gw.get_positions()

        assert len(positions) == 1
        assert positions[0].symbol == "AAPL"
        assert positions[0].qty == 100
        assert positions[0].avg_cost == 150.0
        assert positions[0].unrealized_pnl == 1_000.0
