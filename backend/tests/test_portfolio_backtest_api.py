"""组合回测 API 测试（Wave K-c 遗留项）

覆盖三块：
1. 输入校验（去重后 <2 标的、超上限、未知策略/方法、A股频率、日期顺序）
2. 取数容错（单标的取不到 → 跳过并告警；可用标的 <2 → 422）
3. 返回体的组合独有字段（逐标的归因 + 日度盈亏拆解）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints.portfolio_backtest import (
    MAX_PORTFOLIO_SYMBOLS,
    get_service,
)
from app.data.models import Bar, Frequency, Market
from app.main import app

_START = datetime(2024, 1, 2, tzinfo=UTC)

_BASE_BODY = {
    "strategy_name": "double_ma",
    "symbols": ["AAPL", "MSFT"],
    "market": "US",
    "frequency": "1d",
    "start_date": "2024-01-01",
    "end_date": "2024-06-30",
    "initial_cash": 1_000_000,
}


def _bars(symbol: str, n: int = 120, drift: float = 0.4) -> list[Bar]:
    """构造 n 根合成日线。价格趋势上行，保证均线策略能产生交叉信号。"""
    out: list[Bar] = []
    price = 100.0
    for i in range(n):
        # 用确定性的锯齿而非随机：不同标的错开相位，让组合里出现轮动
        price += drift * (1 if (i // 7) % 2 == 0 else -1)
        out.append(
            Bar(
                time=_START + timedelta(days=i),
                symbol=symbol,
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1_000_000,
            )
        )
    return out


def _service(bars_by_symbol: dict[str, list[Bar]], failing: set[str] | None = None):
    """DataService 替身：按 symbol 返回预置 bar，failing 里的标的抛错。"""
    failing = failing or set()

    async def _get_bars(*, symbol: str, **_kwargs):
        if symbol in failing:
            raise RuntimeError("数据源不可用")
        return bars_by_symbol.get(symbol, [])

    svc = MagicMock()
    svc.get_bars = AsyncMock(side_effect=_get_bars)
    return svc


async def _post(body: dict) -> tuple[int, dict]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/v1/backtests/portfolio", json=body)
    return resp.status_code, resp.json()


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ── 输入校验 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_distinct_symbol_is_rejected() -> None:
    # Arrange：去重后只剩一个标的 —— 本质是单标的回测
    body = _BASE_BODY | {"symbols": ["AAPL", "aapl", " AAPL "]}

    # Act
    status, payload = await _post(body)

    # Assert
    assert status == 422
    assert "至少需要 2 个不同标的" in str(payload)


@pytest.mark.asyncio
async def test_too_many_symbols_is_rejected() -> None:
    body = _BASE_BODY | {"symbols": [f"S{i}" for i in range(MAX_PORTFOLIO_SYMBOLS + 1)]}

    status, payload = await _post(body)

    assert status == 422
    assert str(MAX_PORTFOLIO_SYMBOLS) in str(payload)


@pytest.mark.asyncio
async def test_unknown_strategy_is_rejected() -> None:
    app.dependency_overrides[get_service] = lambda: _service({})

    status, payload = await _post(_BASE_BODY | {"strategy_name": "不存在的策略"})

    assert status == 400
    assert "未知策略" in payload["detail"]


@pytest.mark.asyncio
async def test_unknown_portfolio_method_is_rejected() -> None:
    app.dependency_overrides[get_service] = lambda: _service({})

    status, payload = await _post(_BASE_BODY | {"portfolio_method": "hrp"})

    assert status == 400
    # 优化器类方法有另一个端点，错误信息要把用户指过去
    assert "/factors/strategy/backtest" in payload["detail"]


@pytest.mark.asyncio
async def test_a_share_intraday_frequency_is_rejected() -> None:
    app.dependency_overrides[get_service] = lambda: _service({})

    status, payload = await _post(
        _BASE_BODY | {"market": "A", "frequency": "1m", "symbols": ["600519", "000001"]}
    )

    assert status == 400
    assert "A股仅支持日线" in payload["detail"]


@pytest.mark.asyncio
async def test_inverted_date_range_is_rejected() -> None:
    app.dependency_overrides[get_service] = lambda: _service({})

    status, payload = await _post(
        _BASE_BODY | {"start_date": "2024-06-30", "end_date": "2024-01-01"}
    )

    assert status == 400
    assert "早于" in payload["detail"]


# ── 取数容错 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_failing_symbol_is_skipped_with_warning() -> None:
    # Arrange：三个标的，其中一个取数抛错
    bars = {s: _bars(s) for s in ("AAPL", "MSFT", "NVDA")}
    app.dependency_overrides[get_service] = lambda: _service(bars, failing={"NVDA"})

    # Act
    status, payload = await _post(_BASE_BODY | {"symbols": ["AAPL", "MSFT", "NVDA"]})

    # Assert：回测照常完成，失败标的进 warnings 而不是让整体 500
    assert status == 200
    assert payload["symbols"] == ["AAPL", "MSFT"]
    assert any("NVDA" in w for w in payload["warnings"])


@pytest.mark.asyncio
async def test_symbol_with_too_few_bars_is_skipped() -> None:
    bars = {"AAPL": _bars("AAPL"), "MSFT": _bars("MSFT"), "TSLA": _bars("TSLA", n=1)}
    app.dependency_overrides[get_service] = lambda: _service(bars)

    status, payload = await _post(_BASE_BODY | {"symbols": ["AAPL", "MSFT", "TSLA"]})

    assert status == 200
    assert "TSLA" not in payload["symbols"]
    assert any("TSLA" in w and "bar 不足" in w for w in payload["warnings"])


@pytest.mark.asyncio
async def test_fewer_than_two_usable_symbols_returns_422() -> None:
    # Arrange：两个标的都取不到数
    app.dependency_overrides[get_service] = lambda: _service({}, failing={"AAPL", "MSFT"})

    status, payload = await _post(_BASE_BODY)

    assert status == 422
    assert "可用标的不足" in payload["detail"]


# ── 返回体：组合回测独有的两块 ──────────────────────────────────


@pytest.mark.asyncio
async def test_response_carries_per_symbol_attribution_and_daily_pnl() -> None:
    # Arrange
    bars = {s: _bars(s, drift=0.4 + i * 0.2) for i, s in enumerate(("AAPL", "MSFT"))}
    app.dependency_overrides[get_service] = lambda: _service(bars)

    # Act
    status, payload = await _post(_BASE_BODY)

    # Assert：这两块是 K-c 专为归因建的，单标的端点没有
    assert status == 200
    assert set(payload["per_symbol_metrics"]) == {"AAPL", "MSFT"}
    assert payload["daily_results"], "日度盈亏拆解不应为空"

    assert {"date", "net_pnl", "turnover", "contracts"} <= set(payload["daily_results"][0])

    # 首日通常还没有持仓，取第一个真正有逐标的明细的交易日
    with_contracts = next(
        (row for row in payload["daily_results"] if row["contracts"]), None
    )
    assert with_contracts is not None, "整轮回测都没有任何持仓，样本无效"
    contract = next(iter(with_contracts["contracts"].values()))
    assert {"close_price", "end_pos", "net_pnl"} <= set(contract)
    # 逐标的当日盈亏保留，但不重复嵌成交明细（fills 字段已经给了）
    assert "trades" not in contract


@pytest.mark.asyncio
async def test_capital_is_shared_across_symbols() -> None:
    """组合语义的核心：N 个标的共用一份资金，不是各自独立回测。"""
    bars = {s: _bars(s, drift=0.4 + i * 0.2) for i, s in enumerate(("AAPL", "MSFT"))}
    app.dependency_overrides[get_service] = lambda: _service(bars)

    status, payload = await _post(_BASE_BODY)

    assert status == 200
    # 期末净值是**整个组合**的，不该是两个标的各自 initial_cash 的和
    assert payload["initial_cash"] == _BASE_BODY["initial_cash"]
    assert payload["final_value"] < _BASE_BODY["initial_cash"] * 2


@pytest.mark.asyncio
async def test_max_open_positions_is_forwarded_to_the_engine() -> None:
    bars = {s: _bars(s, drift=0.4 + i * 0.2) for i, s in enumerate(("AAPL", "MSFT", "NVDA"))}
    app.dependency_overrides[get_service] = lambda: _service(bars)

    status, payload = await _post(
        _BASE_BODY | {"symbols": ["AAPL", "MSFT", "NVDA"], "max_open_positions": 1}
    )

    assert status == 200
    # 任一时点的持仓标的数不得超过 1
    for row in payload["daily_results"]:
        held = [c for c in row["contracts"].values() if c["end_pos"] != 0]
        assert len(held) <= 1


# ── 返回体：Wave N-a 三个可空 section ────────────────────────────


@pytest.mark.asyncio
async def test_response_carries_wave_na_sections() -> None:
    # Arrange
    bars = {s: _bars(s, drift=0.4 + i * 0.2) for i, s in enumerate(("AAPL", "MSFT"))}
    app.dependency_overrides[get_service] = lambda: _service(bars)

    # Act
    status, payload = await _post(_BASE_BODY)

    # Assert
    assert status == 200
    assert {"capacity_analysis", "crisis_windows", "rejected_signals"} <= set(payload)

    capacity = payload["capacity_analysis"]
    assert capacity is not None, "有日结数据就该出容量 section"
    assert capacity["capacity"]["is_rough_estimate"] is True
    assert capacity["capacity"]["assumptions"], "容量粗估必须写明前提"
    assert capacity["leverage"]["max"] >= 0

    crisis = payload["crisis_windows"]
    assert crisis is not None
    # 合成行情不落在任何危机区间内 → 全部跳过，且**不出现 0 行**
    assert crisis["windows"] == []
    assert crisis["skipped"], "被跳过的窗口要带理由列出来"
