"""
V3 Wave A-b（G2）：组合再平衡执行 API 测试。

覆盖契约第五节验收项：
- preview → execute 正常链路
- 令牌过期 → 拒绝
- 两步之间持仓变化 → 拒绝
- 部分腿被 OMS 拒 → 两个数组都正确，不整批回滚
- execute 确实走 `OrderManager.submit_order`（断言调用路径）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints.auth import UserInfo, get_current_user
from app.api.v1.endpoints.orders import get_oms
from app.main import app
from app.oms import rebalance_token
from app.oms.manager import OrderManager, RiskViolationError
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus

PREVIEW_URL = "/api/v1/portfolio/rebalance/preview"
EXECUTE_URL = "/api/v1/portfolio/rebalance/execute"


def _admin_user() -> UserInfo:
    return UserInfo(id="test-admin", email="admin@test.local", role="admin")


def _viewer_user() -> UserInfo:
    return UserInfo(id="test-viewer", email="viewer@test.local", role="viewer")


def _position(symbol: str, qty: int, price: float) -> dict:
    return {
        "symbol": symbol,
        "market": "US",
        "qty": qty,
        "avg_cost": price,
        "current_price": price,
        "market_value": qty * price,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": 0.0,
    }


def _make_order(symbol: str = "AAA", **kwargs: Any) -> LiveOrder:
    defaults: dict[str, Any] = {
        "symbol": symbol,
        "market": "US",
        "side": LiveOrderSide.BUY,
        "qty": 10,
        "status": LiveOrderStatus.SUBMITTED,
        "broker_order_id": "broker-1",
    }
    defaults.update(kwargs)
    return LiveOrder(**defaults)


@pytest.fixture
def mock_oms() -> MagicMock:
    """持仓：AAA 100 股 @100（1 万），BBB 100 股 @50（5 千）；净值 2 万。"""
    oms = MagicMock(spec=OrderManager)
    oms.get_positions = AsyncMock(return_value=[
        _position("AAA", 100, 100.0),
        _position("BBB", 100, 50.0),
    ])
    oms.get_account = AsyncMock(return_value={
        "account_id": "test-acc",
        "currency": "USD",
        "cash": 5_000.0,
        "buying_power": 10_000.0,
        "portfolio_value": 20_000.0,
    })
    oms.submit_order = AsyncMock(side_effect=lambda **kw: _make_order(**{
        "symbol": kw["symbol"], "side": kw["side"], "qty": kw["qty"],
    }))
    return oms


@pytest.fixture(autouse=True)
def override_deps(mock_oms: MagicMock):
    app.dependency_overrides[get_oms] = lambda: mock_oms
    app.dependency_overrides[get_current_user] = _admin_user
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _preview(client: AsyncClient, **overrides: Any) -> dict:
    body: dict[str, Any] = {
        "target_weights": {"AAA": 0.75, "BBB": 0.25},
        "market": "US",
    }
    body.update(overrides)
    resp = await client.post(PREVIEW_URL, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── 1. 预览：股数级增量 ───────────────────────────────────────────


@pytest.mark.asyncio
class TestPreview:
    async def test_returns_share_level_diff_legs(self, client: AsyncClient) -> None:
        # Arrange：净值 2 万，AAA 目标 75% → 150 股（已有 100）；BBB 目标 25% → 100 股（已有 100）
        # Act
        data = await _preview(client)

        # Assert
        legs = {leg["symbol"]: leg for leg in data["legs"]}
        assert legs["AAA"]["delta_qty"] == 50        # 不是 150
        assert legs["AAA"]["side"] == "BUY"
        assert legs["AAA"]["reason"] == "increase"
        assert "BBB" not in legs                     # 已在目标上，无需交易
        assert data["total_buy_value"] == pytest.approx(5_000.0)
        assert data["total_sell_value"] == 0.0
        assert data["confirm_token"]
        assert data["expires_in_seconds"] == rebalance_token.REBALANCE_TOKEN_TTL_SECONDS

    async def test_zero_weight_produces_close_leg(self, client: AsyncClient) -> None:
        data = await _preview(client, target_weights={"AAA": 1.0, "BBB": 0.0})

        legs = {leg["symbol"]: leg for leg in data["legs"]}
        assert legs["BBB"]["delta_qty"] == -100
        assert legs["BBB"]["reason"] == "close"
        assert data["legs"][0]["side"] == "SELL"     # 先卖后买
        assert data["total_sell_value"] == pytest.approx(5_000.0)

    async def test_estimated_commission_is_reported(self, client: AsyncClient) -> None:
        data = await _preview(client)
        assert data["estimated_commission"] > 0

    async def test_weight_sum_not_one_rejected(self, client: AsyncClient) -> None:
        resp = await client.post(PREVIEW_URL, json={
            "target_weights": {"AAA": 0.5, "BBB": 0.25}, "market": "US",
        })
        assert resp.status_code == 400
        assert "权重和" in resp.json()["detail"]

    async def test_untargeted_holding_produces_warning(self, client: AsyncClient) -> None:
        data = await _preview(client, target_weights={"AAA": 1.0})
        assert any("BBB" in w for w in data["warnings"])

    async def test_lot_size_rounds_to_board_lot(self, client: AsyncClient) -> None:
        # AAA 目标 75% × 2万 / 100 = 150 股 → 整手 100 → 100 股 → delta 0
        data = await _preview(client, lot_size=100)
        assert {leg["symbol"] for leg in data["legs"]} == set()

    async def test_min_trade_value_filters_odd_lots(self, client: AsyncClient) -> None:
        data = await _preview(client, min_trade_value=6_000.0)
        assert data["legs"] == []

    async def test_zero_portfolio_value_rejected(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        mock_oms.get_account = AsyncMock(return_value={
            "account_id": "a", "currency": "USD", "cash": 0.0,
            "buying_power": 0.0, "portfolio_value": 0.0,
        })
        resp = await client.post(PREVIEW_URL, json={
            "target_weights": {"AAA": 1.0}, "market": "US",
        })
        assert resp.status_code == 422

    async def test_broker_failure_maps_to_503(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        mock_oms.get_positions = AsyncMock(side_effect=RuntimeError("gateway down"))
        resp = await client.post(PREVIEW_URL, json={
            "target_weights": {"AAA": 1.0}, "market": "US",
        })
        assert resp.status_code == 503


# ── 2. preview → execute 正常链路 ─────────────────────────────────


@pytest.mark.asyncio
class TestExecuteHappyPath:
    async def test_full_chain_submits_via_order_manager(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        # Arrange
        preview = await _preview(client)

        # Act
        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        # Assert
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["submitted"]) == 1
        assert data["rejected"] == []
        assert data["submitted"][0]["symbol"] == "AAA"
        assert data["submitted"][0]["order_id"]

        # 必须走 OMS（拿到风控/熔断/审计），不得直连 gateway
        mock_oms.submit_order.assert_awaited_once()
        kwargs = mock_oms.submit_order.await_args.kwargs
        assert kwargs["symbol"] == "AAA"
        assert kwargs["market"] == "US"
        assert kwargs["side"] == LiveOrderSide.BUY
        assert kwargs["qty"] == 50

    async def test_sell_leg_submits_absolute_qty(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        preview = await _preview(client, target_weights={"AAA": 1.0, "BBB": 0.0})

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        assert resp.status_code == 200, resp.text
        sell_call = next(
            c for c in mock_oms.submit_order.await_args_list
            if c.kwargs["symbol"] == "BBB"
        )
        assert sell_call.kwargs["side"] == LiveOrderSide.SELL
        assert sell_call.kwargs["qty"] == 100      # 绝对值，不是 -100

    async def test_viewer_role_is_forbidden(self, client: AsyncClient) -> None:
        preview = await _preview(client)
        app.dependency_overrides[get_current_user] = _viewer_user

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        assert resp.status_code == 403


# ── 3. confirm_token 校验 ────────────────────────────────────────


@pytest.mark.asyncio
class TestConfirmToken:
    async def test_expired_token_is_rejected(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preview = await _preview(client)

        # 时间快进到 TTL 之外
        real_time = rebalance_token.time.time
        monkeypatch.setattr(
            rebalance_token.time,
            "time",
            lambda: real_time() + rebalance_token.REBALANCE_TOKEN_TTL_SECONDS + 5,
        )

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        assert resp.status_code == 409
        assert "过期" in resp.json()["detail"]

    async def test_positions_changed_between_steps_is_rejected(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        preview = await _preview(client)

        # 两步之间有别的成交把 AAA 打到 120 股
        mock_oms.get_positions = AsyncMock(return_value=[
            _position("AAA", 120, 100.0),
            _position("BBB", 100, 50.0),
        ])

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        assert resp.status_code == 409
        assert "持仓" in resp.json()["detail"]
        mock_oms.submit_order.assert_not_awaited()

    async def test_tampered_legs_are_rejected(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        preview = await _preview(client)
        tampered = [dict(preview["legs"][0], delta_qty=5_000)]

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": tampered,
            "confirm_token": preview["confirm_token"],
        })

        assert resp.status_code == 409
        mock_oms.submit_order.assert_not_awaited()

    async def test_forged_token_is_rejected(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        preview = await _preview(client)
        forged = preview["confirm_token"][:-4] + "dead"

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": forged,
        })

        assert resp.status_code == 409
        assert "签名" in resp.json()["detail"]
        mock_oms.submit_order.assert_not_awaited()

    async def test_missing_token_is_rejected_by_schema(self, client: AsyncClient) -> None:
        preview = await _preview(client)
        resp = await client.post(EXECUTE_URL, json={
            "market": "US", "legs": preview["legs"],
        })
        assert resp.status_code == 422

    async def test_token_from_other_market_is_rejected(self, client: AsyncClient) -> None:
        preview = await _preview(client)
        resp = await client.post(EXECUTE_URL, json={
            "market": "HK",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })
        assert resp.status_code == 409


# ── 4. 部分失败：如实返回，不整批回滚 ────────────────────────────


@pytest.mark.asyncio
class TestPartialFailure:
    async def test_rejected_leg_does_not_roll_back_successful_leg(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        # Arrange：AAA 卖出成功，BBB 买入被风控拒
        preview = await _preview(client, target_weights={"AAA": 0.25, "BBB": 0.75})
        assert len(preview["legs"]) == 2

        async def _submit(**kw):
            if kw["symbol"] == "BBB":
                raise RiskViolationError("单笔股数超上限")
            return _make_order(symbol=kw["symbol"], side=kw["side"], qty=kw["qty"])

        mock_oms.submit_order = AsyncMock(side_effect=_submit)

        # Act
        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        # Assert：两个数组都要填，成功的那腿不撤单
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert [s["symbol"] for s in data["submitted"]] == ["AAA"]
        assert [r["symbol"] for r in data["rejected"]] == ["BBB"]
        assert "单笔股数超上限" in data["rejected"][0]["reason"]
        assert mock_oms.cancel_order.await_count == 0

    async def test_broker_rejected_status_lands_in_rejected_array(
        self, client: AsyncClient, mock_oms: MagicMock
    ) -> None:
        preview = await _preview(client)
        mock_oms.submit_order = AsyncMock(return_value=_make_order(
            status=LiveOrderStatus.REJECTED, reject_reason="[PROTECTION:cooldown] 冷却中",
        ))

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        data = resp.json()
        assert data["submitted"] == []
        assert data["rejected"][0]["reason"] == "[PROTECTION:cooldown] 冷却中"

    async def test_zero_delta_leg_is_rejected_upfront(self, client: AsyncClient) -> None:
        preview = await _preview(client)
        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": [dict(preview["legs"][0], delta_qty=0)],
            "confirm_token": preview["confirm_token"],
        })
        assert resp.status_code == 400


# ── Wave O-a / O4：rebalance_executed 通知 ────────────────────


@pytest.mark.asyncio
class TestRebalanceExecutedNotification:
    async def test_execute_emits_rebalance_executed(
        self, client: AsyncClient, mock_oms: MagicMock, monkeypatch
    ) -> None:
        emitted: list[dict] = []
        monkeypatch.setattr(
            "app.api.v1.endpoints.rebalance.emit_rebalance_executed",
            lambda **kwargs: emitted.append(kwargs) or {},
        )
        preview = await _preview(client)

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
            "strategy_id": "s-1",
        })

        assert resp.status_code == 200, resp.text
        assert emitted == [
            {"market": "US", "submitted": 1, "rejected": 0, "strategy_id": "s-1"}
        ]

    async def test_notification_failure_does_not_break_execute(
        self, client: AsyncClient, mock_oms: MagicMock, monkeypatch
    ) -> None:
        """通知是旁路：dispatch 炸了不该把已经下出去的单变成 500。"""
        def _boom(*_a, **_kw):
            raise RuntimeError("Redis 挂了")

        monkeypatch.setattr("app.notify.dispatcher.dispatch_event", _boom)
        preview = await _preview(client)

        resp = await client.post(EXECUTE_URL, json={
            "market": "US",
            "legs": preview["legs"],
            "confirm_token": preview["confirm_token"],
        })

        assert resp.status_code == 200, resp.text
        assert len(resp.json()["submitted"]) == 1
