"""
Phase 1 bars API 端点测试

使用 FastAPI dependency_overrides 注入 mock DataService，
避免实际数据库连接。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.data.models import Bar, Frequency, Market
from app.main import app


def _make_bar(symbol: str = "AAPL", market: Market = Market.US) -> Bar:
    return Bar(
        time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        symbol=symbol,
        market=market,
        frequency=Frequency.DAY_1,
        open=180.0,
        high=185.0,
        low=179.0,
        close=183.5,
        volume=1_000_000,
        vwap=182.0,
    )


def _mock_service(bars: list[Bar] | None = None) -> MagicMock:
    """创建 mock DataService，返回指定 bars 列表。"""
    svc = MagicMock()
    svc.get_bars = AsyncMock(return_value=bars or [])
    svc.get_latest_bar = AsyncMock(return_value=bars[0] if bars else None)
    svc.search_symbols = AsyncMock(return_value=[])
    svc.backfill = AsyncMock(return_value=0)
    return svc


def _override_service(svc: MagicMock):
    """FastAPI dependency override 工厂。"""
    from app.api.v1.endpoints.bars import get_service

    def _override():
        return svc

    app.dependency_overrides[get_service] = _override
    return svc


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_get_bars_returns_data() -> None:
    svc = _override_service(_mock_service([_make_bar("AAPL")]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=AAPL&market=US&frequency=1d")

    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "AAPL"
    assert data["market"] == "US"
    assert data["count"] == 1
    assert data["bars"][0]["close"] == 183.5
    assert data["bars"][0]["vwap"] == 182.0


@pytest.mark.asyncio
async def test_get_bars_returns_empty_list_when_no_data() -> None:
    _override_service(_mock_service([]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=AAPL&market=US")

    assert response.status_code == 200
    assert response.json()["count"] == 0


@pytest.mark.asyncio
async def test_get_bars_rejects_invalid_market() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=AAPL&market=INVALID")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_bars_rejects_start_after_end() -> None:
    svc = _mock_service([])
    svc.get_bars = AsyncMock(side_effect=ValueError("start must be before end"))
    _override_service(svc)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/bars?symbol=AAPL&market=US&start=2024-01-31&end=2024-01-01"
        )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_bars_respects_limit_param() -> None:
    bars = [_make_bar() for _ in range(10)]
    _override_service(_mock_service(bars))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=AAPL&market=US&limit=3")

    assert response.json()["count"] == 3


@pytest.mark.asyncio
async def test_get_bars_hk_market() -> None:
    hk_bar = Bar(
        time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        symbol="00700",
        market=Market.HK,
        frequency=Frequency.DAY_1,
        open=350.0, high=360.0, low=348.0, close=355.0, volume=5_000_000,
    )
    _override_service(_mock_service([hk_bar]))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=00700&market=HK")

    assert response.status_code == 200
    data = response.json()
    assert data["market"] == "HK"
    assert data["bars"][0]["close"] == 355.0


@pytest.mark.asyncio
async def test_get_bars_feed_error_returns_503() -> None:
    svc = _mock_service([])
    svc.get_bars = AsyncMock(side_effect=Exception("Feed unavailable"))
    _override_service(svc)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/bars?symbol=AAPL&market=US")

    assert response.status_code == 503
