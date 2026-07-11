import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock

from app.main import app


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_check() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_bars_endpoint_returns_empty() -> None:
    from app.api.v1.endpoints.bars import get_service
    mock_svc = MagicMock()
    mock_svc.get_bars = AsyncMock(return_value=[])
    app.dependency_overrides[get_service] = lambda: mock_svc

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=AAPL&market=US&frequency=1d")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "AAPL"
    assert isinstance(data["bars"], list)


@pytest.mark.asyncio
async def test_strategy_presets_returns_list() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/strategies/presets")
    assert response.status_code == 200
    presets = response.json()
    assert len(presets) >= 8   # 已从 8 扩展至 16 种策略
    preset_ids = {p["name"] for p in presets}   # 预设以 name 为标识
    assert "double_ma" in preset_ids
    assert "bollinger" in preset_ids
    assert "multi_factor" in preset_ids


@pytest.mark.asyncio
async def test_risk_config_returns_default() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/risk")
    assert response.status_code == 200
    config = response.json()
    assert len(config["rules"]) > 0
    rule_types = {r["rule_type"] for r in config["rules"]}
    assert "max_drawdown" in rule_types
    assert "daily_loss_limit" in rule_types
