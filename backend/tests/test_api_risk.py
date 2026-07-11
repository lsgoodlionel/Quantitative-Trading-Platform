"""风控 API 端点测试"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.risk.engine import init_risk_engine
from app.api.v1.endpoints.auth import get_current_user, UserInfo


@pytest.fixture(autouse=True)
def fresh_risk_engine():
    """每个测试用新引擎，避免日计数器等状态污染。"""
    init_risk_engine()
    # PUT /risk 需 Trader 角色；覆盖 get_current_user 为管理员绕过 RBAC
    app.dependency_overrides[get_current_user] = lambda: UserInfo(
        id="test-admin", email="admin@test.local", role="admin"
    )
    yield
    app.dependency_overrides.clear()


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestGetRiskConfig:
    @pytest.mark.asyncio
    async def test_returns_default_config(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/risk")
        assert resp.status_code == 200
        body = resp.json()
        assert "rules" in body
        assert len(body["rules"]) > 0
        assert body["is_active"] is True


class TestUpdateRiskConfig:
    @pytest.mark.asyncio
    async def test_update_config(self, client: AsyncClient) -> None:
        resp = await client.put("/api/v1/risk", json={
            "name": "custom",
            "rules": [
                {"rule_type": "max_order_value", "value": 200_000, "enabled": True, "severity": "block"},
            ],
            "is_active": True,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "custom"
        assert len(body["rules"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_rule_type_returns_400(self, client: AsyncClient) -> None:
        resp = await client.put("/api/v1/risk", json={
            "name": "bad",
            "rules": [{"rule_type": "nonexistent_rule", "value": 100}],
            "is_active": True,
        })
        assert resp.status_code == 400


class TestPreTradeCheck:
    @pytest.mark.asyncio
    async def test_passes_valid_order(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/risk/check/pre-trade", json={
            "symbol": "AAPL",
            "market": "US",
            "side": "BUY",
            "qty": 10,
            "price": 150.0,
            "portfolio_value": 100_000.0,
            "current_symbol_value": 0.0,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] is True
        assert body["violations"] == []

    @pytest.mark.asyncio
    async def test_blocks_oversized_order(self, client: AsyncClient) -> None:
        # 设置低上限
        await client.put("/api/v1/risk", json={
            "name": "test",
            "rules": [{"rule_type": "max_order_value", "value": 1000, "severity": "block"}],
            "is_active": True,
        })
        resp = await client.post("/api/v1/risk/check/pre-trade", json={
            "symbol": "AAPL",
            "market": "US",
            "side": "BUY",
            "qty": 100,
            "price": 150.0,       # 15,000 > 1,000
            "portfolio_value": 100_000.0,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["passed"] is False
        assert len(body["violations"]) > 0

    @pytest.mark.asyncio
    async def test_check_returns_violation_details(self, client: AsyncClient) -> None:
        await client.put("/api/v1/risk", json={
            "name": "test",
            "rules": [{"rule_type": "max_order_value", "value": 500, "severity": "block"}],
            "is_active": True,
        })
        resp = await client.post("/api/v1/risk/check/pre-trade", json={
            "symbol": "AAPL", "market": "US", "side": "BUY",
            "qty": 10, "price": 100.0, "portfolio_value": 100_000.0,
        })
        body = resp.json()
        assert not body["passed"]
        v = body["violations"][0]
        assert "rule_type" in v
        assert "message" in v
        assert "value_actual" in v


class TestPortfolioCheck:
    @pytest.mark.asyncio
    async def test_passes_healthy_portfolio(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/risk/check/portfolio", json={
            "portfolio_value": 100_000.0,
            "initial_value": 100_000.0,
            "positions": [{"symbol": "AAPL", "market_value": 20_000}],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "violations" in body
        assert "daily_summary" in body

    @pytest.mark.asyncio
    async def test_detects_concentration_violation(self, client: AsyncClient) -> None:
        # 单标的占 80% → 超过默认 30% 限制
        resp = await client.post("/api/v1/risk/check/portfolio", json={
            "portfolio_value": 100_000.0,
            "initial_value": 100_000.0,
            "positions": [{"symbol": "AAPL", "market_value": 80_000.0}],
        })
        body = resp.json()
        rule_types = [v["rule_type"] for v in body["violations"]]
        assert "position_concentration" in rule_types


class TestRiskSummary:
    @pytest.mark.asyncio
    async def test_returns_summary_fields(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/risk/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert "orders_today" in body
        assert "realized_pnl_today" in body


class TestPortfolioOptimize:
    @pytest.mark.asyncio
    async def test_optimize_equal_weight(self, client: AsyncClient) -> None:
        import numpy as np
        rng = np.random.default_rng(42)
        n = 60
        prices = {
            sym: (100 * np.cumprod(1 + rng.normal(0.001, 0.015, n))).tolist()
            for sym in ["AAPL", "MSFT", "GOOGL"]
        }
        resp = await client.post("/api/v1/risk/portfolio/optimize", json={
            "prices": prices,
            "mode": "equal_weight",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "weights" in body
        total = sum(body["weights"].values())
        assert abs(total - 1.0) < 1e-4

    @pytest.mark.asyncio
    async def test_optimize_risk_parity(self, client: AsyncClient) -> None:
        import numpy as np
        rng = np.random.default_rng(0)
        n = 60
        prices = {
            sym: (100 * np.cumprod(1 + rng.normal(0.001, 0.015, n))).tolist()
            for sym in ["AAPL", "MSFT"]
        }
        resp = await client.post("/api/v1/risk/portfolio/optimize", json={
            "prices": prices,
            "mode": "risk_parity",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_optimize_rejects_single_symbol(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/risk/portfolio/optimize", json={
            "prices": {"AAPL": [100.0] * 60},
            "mode": "equal_weight",
        })
        assert resp.status_code == 400


class TestRebalance:
    @pytest.mark.asyncio
    async def test_compute_rebalance(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/risk/portfolio/rebalance", json={
            "current_positions": {"AAPL": 80_000, "MSFT": 20_000},
            "target_weights": {"AAPL": 0.5, "MSFT": 0.5},
            "portfolio_value": 100_000,
            "min_trade_value": 100.0,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "orders" in body
        assert body["total_sell"] > 0  # AAPL 需要卖出
        assert body["total_buy"] > 0   # MSFT 需要买入
