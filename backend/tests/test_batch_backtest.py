"""
批量回测 API 测试（V3 G3）

覆盖契约三条硬要求：
1. 超过 MAX_BATCH_SYMBOLS 返回明确错误
2. 单个标的失败时其余照常返回，该项带 error 字段
3. 通知发送失败时回测结果仍正常返回（通知是旁路）
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints.backtest_batch import MAX_BATCH_SYMBOLS, get_service
from app.data.models import Bar, Frequency, Market
from app.main import app

_BASE_BODY = {
    "strategy_name": "double_ma",
    "market": "US",
    "frequency": "1d",
    "start_date": "2024-01-01",
    "end_date": "2024-03-01",
    "initial_cash": 100000,
    "params": {},
}


def _make_bars(symbol: str, n: int = 60) -> list[Bar]:
    """构造 n 根合成日线（价格缓慢上行，足够跑通任意均线策略）。"""
    return [
        Bar(
            time=datetime(2024, 1, 1, tzinfo=UTC).replace(day=1 + i % 28, month=1 + i // 28),
            symbol=symbol,
            market=Market.US,
            frequency=Frequency.DAY_1,
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1_000_000,
            vwap=100.2 + i,
        )
        for i in range(n)
    ]


def _service_with(bars_by_symbol: dict[str, list[Bar]], failures: dict[str, Exception] | None = None):
    """mock DataService：按 symbol 返回 bars，或抛出指定异常。"""
    failures = failures or {}

    async def _get_bars(*, symbol: str, **_kwargs):
        if symbol in failures:
            raise failures[symbol]
        return bars_by_symbol.get(symbol, [])

    svc = MagicMock()
    svc.get_bars = AsyncMock(side_effect=_get_bars)
    return svc


def _override(svc) -> None:
    app.dependency_overrides[get_service] = lambda: svc


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _post(body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/v1/backtests/batch", json=body)


# ── 1. 数量上限 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rejects_more_than_max_batch_symbols() -> None:
    _override(_service_with({}))
    symbols = [f"SYM{i}" for i in range(MAX_BATCH_SYMBOLS + 1)]

    response = await _post({**_BASE_BODY, "symbols": symbols})

    assert response.status_code == 400
    assert str(MAX_BATCH_SYMBOLS) in response.json()["detail"]


@pytest.mark.asyncio
async def test_accepts_exactly_max_batch_symbols() -> None:
    """恰好等于上限不应被拒（边界值）。"""
    symbols = [f"SYM{i}" for i in range(MAX_BATCH_SYMBOLS)]
    _override(_service_with({s: _make_bars(s) for s in symbols}))

    response = await _post({**_BASE_BODY, "symbols": symbols})

    assert response.status_code == 200
    assert response.json()["total"] == MAX_BATCH_SYMBOLS


@pytest.mark.asyncio
async def test_rejects_empty_symbols() -> None:
    _override(_service_with({}))

    response = await _post({**_BASE_BODY, "symbols": []})

    assert response.status_code == 422  # pydantic min_length


@pytest.mark.asyncio
async def test_deduplicates_symbols() -> None:
    _override(_service_with({"AAPL": _make_bars("AAPL")}))

    response = await _post({**_BASE_BODY, "symbols": ["AAPL", "aapl", " AAPL "]})

    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.asyncio
async def test_rejects_unknown_strategy() -> None:
    _override(_service_with({"AAPL": _make_bars("AAPL")}))

    response = await _post({**_BASE_BODY, "strategy_name": "nope", "symbols": ["AAPL"]})

    assert response.status_code == 400
    assert "Unknown strategy" in response.json()["detail"]


# ── 2. 单标的失败隔离 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_symbol_failure_does_not_fail_batch() -> None:
    """BAD 取数抛错，其余标的照常返回；失败项带 error 字段。"""
    svc = _service_with(
        {"AAPL": _make_bars("AAPL"), "MSFT": _make_bars("MSFT")},
        failures={"BAD": RuntimeError("数据源炸了")},
    )
    _override(svc)

    response = await _post({**_BASE_BODY, "symbols": ["AAPL", "BAD", "MSFT"]})

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["succeeded"] == 2
    assert data["failed"] == 1

    by_symbol = {r["symbol"]: r for r in data["results"]}
    assert by_symbol["BAD"]["error"] is not None
    assert by_symbol["BAD"]["metrics"] is None
    assert by_symbol["AAPL"]["error"] is None
    assert by_symbol["AAPL"]["metrics"]["total_trades"] >= 0
    assert by_symbol["MSFT"]["error"] is None


@pytest.mark.asyncio
async def test_insufficient_bars_reported_as_error_item() -> None:
    """数据不足（<5 根）应作为该标的的 error，而非整批 422。"""
    _override(_service_with({"AAPL": _make_bars("AAPL"), "THIN": _make_bars("THIN", 2)}))

    response = await _post({**_BASE_BODY, "symbols": ["AAPL", "THIN"]})

    assert response.status_code == 200
    by_symbol = {r["symbol"]: r for r in response.json()["results"]}
    assert "数据不足" in by_symbol["THIN"]["error"]
    assert by_symbol["AAPL"]["error"] is None


@pytest.mark.asyncio
async def test_all_symbols_failing_still_returns_200() -> None:
    _override(_service_with({}, failures={"A": RuntimeError("x"), "B": RuntimeError("y")}))

    response = await _post({**_BASE_BODY, "symbols": ["A", "B"]})

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 0
    assert data["failed"] == 2
    assert all(r["error"] for r in data["results"])


# ── 3. 通知失败不影响任务结果 ─────────────────────────────────

@pytest.mark.asyncio
async def test_notification_failure_does_not_break_batch(monkeypatch) -> None:
    """dispatch_event 抛异常时，批量回测结果仍正常返回。"""
    def _boom(*_args, **_kwargs):
        raise RuntimeError("Telegram 超时")

    monkeypatch.setattr("app.notify.dispatcher.dispatch_event", _boom)
    _override(_service_with({"AAPL": _make_bars("AAPL")}))

    response = await _post({**_BASE_BODY, "symbols": ["AAPL"]})

    assert response.status_code == 200
    data = response.json()
    assert data["succeeded"] == 1
    assert data["results"][0]["metrics"] is not None
